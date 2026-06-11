from __future__ import annotations

import asyncio
import inspect
from importlib import resources
from pathlib import Path

from loguru import logger
from nicegui import app, events, run as nicegui_run, ui

from fitweavr.routes import (
    ConversionProfile,
    default_profile,
    destination_option,
    get_conversion_profile,
    source_option,
    supported_destinations,
    supported_profiles,
    supported_sources,
)
from fitweavr_web.config import AppSettings
from fitweavr_web.service import WebConversionService
from fitweavr_web.session import (
    FileRecord,
    SessionExpiredError,
    SessionManager,
    SessionState,
    TooManySessionsError,
)


ASSET_ROUTE = "/fitweavr-assets"
LOGO_FILE = "fit_weavr.png"


def create_app(settings: AppSettings | None = None) -> None:
    settings = settings or AppSettings.from_env()
    sessions = SessionManager(settings)
    service = WebConversionService(settings, sessions.store)
    assets_dir = Path(resources.files("fitweavr_web") / "assets")

    app.add_static_files(ASSET_ROUTE, str(assets_dir))

    @ui.page("/")
    def index() -> None:
        ip_address = _client_ip()
        try:
            state = _get_or_create_session(sessions, ip_address)
        except TooManySessionsError as exc:
            _render_limit_page(settings, str(exc))
            return

        _render_main_page(settings, sessions, service, state.id, ip_address)


def run(settings: AppSettings | None = None, *, reload: bool = False) -> None:
    settings = settings or AppSettings.from_env()
    create_app(settings)
    icon_path = Path(resources.files("fitweavr_web") / "assets" / LOGO_FILE)
    ui.run(
        host=settings.host,
        port=settings.port,
        title=settings.app_name,
        favicon=str(icon_path),
        reload=reload,
        show=False,
        storage_secret=settings.storage_secret,
    )


def _get_or_create_session(sessions: SessionManager, ip_address: str) -> SessionState:
    browser = app.storage.browser
    existing_id = browser.get("fitweavr_session_id")
    if existing_id:
        try:
            return sessions.require(str(existing_id), ip_address=ip_address)
        except SessionExpiredError:
            browser.pop("fitweavr_session_id", None)

    state = sessions.create(ip_address=ip_address)
    browser["fitweavr_session_id"] = state.id
    return state


def _render_limit_page(settings: AppSettings, message: str) -> None:
    _add_page_style()
    with ui.column().classes("min-h-screen w-full items-center justify-center bg-white p-6 text-slate-900"):
        ui.image(f"{ASSET_ROUTE}/{LOGO_FILE}").classes("w-64 max-w-full")
        ui.label(settings.app_name).classes("text-3xl font-semibold")
        ui.label(message).classes("text-slate-500")


def _render_main_page(
    settings: AppSettings,
    sessions: SessionManager,
    service: WebConversionService,
    session_id: str,
    ip_address: str,
) -> None:
    _add_page_style()

    queue_list = None
    output_list = None
    route_list = None
    status = None
    progress = None
    progress_label = None
    source_select: ui.select | None = None
    destination_select: ui.select | None = None
    source_description = None
    destination_description = None
    route_description = None
    route_formats = None
    upload_hint = None
    convert_button: ui.button | None = None
    zip_button: ui.button | None = None
    footer_ip = None
    footer_session = None
    footer_remaining = None
    footer_active = None
    queue_refresh_needed = False

    fallback_profile = default_profile()
    browser = app.storage.browser
    initial_source = str(browser.get("fitweavr_source_type", fallback_profile.source_type.value))
    initial_destination = str(browser.get("fitweavr_destination_type", fallback_profile.destination_type.value))
    source_options = {option.source_type.value: option.label for option in supported_sources()}
    destination_options = {
        option.destination_type.value: option.label for option in supported_destinations()
    }
    upload_accept = ",".join(
        sorted({suffix for profile in supported_profiles() for suffix in profile.accepted_suffixes})
    )

    if initial_source not in source_options:
        initial_source = fallback_profile.source_type.value
    if initial_destination not in destination_options:
        initial_destination = fallback_profile.destination_type.value

    def state_or_notify(*, touch: bool = True) -> SessionState | None:
        try:
            return sessions.require(session_id, ip_address=ip_address, touch=touch)
        except SessionExpiredError as exc:
            ui.notify(str(exc), type="warning")
            if status is not None:
                status.text = "Session expired"
            if convert_button is not None:
                convert_button.disable()
            if zip_button is not None:
                zip_button.disable()
            return None

    def selected_profile() -> ConversionProfile:
        source_value = initial_source if source_select is None else str(source_select.value)
        destination_value = (
            initial_destination if destination_select is None else str(destination_select.value)
        )
        return get_conversion_profile(source_value, destination_value)

    def update_route_info() -> None:
        if source_select is None or destination_select is None:
            return
        profile = selected_profile()
        browser["fitweavr_source_type"] = profile.source_type.value
        browser["fitweavr_destination_type"] = profile.destination_type.value

        source = source_option(profile.source_type)
        destination = destination_option(profile.destination_type)
        if source_description is not None:
            source_description.text = source.description
        if destination_description is not None:
            destination_description.text = destination.description
        if route_description is not None:
            route_description.text = profile.description
        if route_formats is not None:
            suffixes = ", ".join(profile.accepted_suffixes)
            route_formats.text = f"{profile.input_format.name} ({suffixes}) -> {profile.target_format.name}"
        if upload_hint is not None:
            upload_hint.text = (
                f"Accepted: {', '.join(profile.accepted_suffixes)}. "
                "Uploads are saved to session temp storage and cleaned after conversion."
            )
        render_routes(profile)

    def render_routes(selected: ConversionProfile) -> None:
        if route_list is None:
            return
        route_list.clear()
        with route_list:
            for profile in supported_profiles():
                active = profile.route_id == selected.route_id
                color = "bg-blue-50 border-blue-300 text-blue-950" if active else "bg-white border-slate-200 text-slate-700"
                with ui.row().classes(
                    f"items-center gap-2 rounded-md border px-3 py-2 text-sm {color}"
                ):
                    ui.icon("check_circle" if active else "swap_horiz").classes(
                        "text-blue-600" if active else "text-slate-400"
                    )
                    ui.label(profile.label).classes("font-medium")

    def refresh_files(*, touch: bool = True) -> None:
        nonlocal queue_refresh_needed
        queue_refresh_needed = False
        state = state_or_notify(touch=touch)
        if queue_list is None or output_list is None:
            return
        queue_list.clear()
        output_list.clear()
        if state is None:
            return

        records = service.list_files(state)
        with queue_list:
            if not records:
                ui.label("No files queued").classes("text-sm text-slate-400")
            for record in records:
                _file_status_row(record)

        with output_list:
            output_count = 0
            for record in records:
                for path in record.output_paths:
                    if not path.exists():
                        continue
                    output_count += 1
                    with ui.row().classes(
                        "w-full items-center justify-between gap-3 rounded-md border border-slate-200 bg-white px-3 py-2"
                    ):
                        with ui.row().classes("min-w-0 items-center gap-2"):
                            ui.icon("task_alt").classes("text-emerald-600")
                            ui.label(path.name).classes("truncate text-sm text-slate-800")
                        ui.button(icon="download", on_click=lambda p=path: download_file(p)).props(
                            "flat dense round"
                        ).tooltip("Download")
            if output_count == 0:
                ui.label("No downloads yet").classes("text-sm text-slate-400")

        has_convertible_inputs = any(record.input_path.exists() for record in records)
        if convert_button is not None:
            convert_button.enable() if has_convertible_inputs else convert_button.disable()
        if zip_button is not None:
            zip_button.enable() if state.archive_path and state.archive_path.exists() else zip_button.disable()
        update_footer()

    def schedule_file_refresh() -> None:
        nonlocal queue_refresh_needed
        queue_refresh_needed = True

    def flush_file_refresh() -> None:
        if queue_refresh_needed:
            refresh_files(touch=False)

    def handle_begin_upload(_: events.UiEventArguments) -> None:
        state = state_or_notify()
        if state is not None and _has_cleaned_batch(service.list_files(state)):
            service.clear(state)
        if status is not None:
            status.text = "Receiving files..."
        if convert_button is not None:
            convert_button.disable()
        if zip_button is not None:
            zip_button.disable()

    async def handle_upload(event: events.UploadEventArguments) -> None:
        state = state_or_notify()
        if state is None:
            return
        target: Path | None = None
        try:
            profile = selected_profile()
            filename, target = await _save_upload_event_to_temp(event, service, state, profile)
            record = service.register_upload(state, filename, target, profile=profile)
            if status is not None:
                status.text = f"Queued {record.original_name}"
            logger.info("Queued upload {}", record.input_path)
            schedule_file_refresh()
        except Exception as exc:
            if target is not None:
                target.unlink(missing_ok=True)
            logger.exception("Upload failed")
            if status is not None:
                status.text = f"Upload failed: {exc}"
            ui.notify(str(exc), type="negative")

    def handle_upload_rejected(_: events.UiEventArguments) -> None:
        ui.notify(f"One or more files were rejected. Max file size is {settings.max_upload_mb} MB.", type="warning")
        schedule_file_refresh()

    async def convert() -> None:
        state = state_or_notify()
        if state is None:
            return

        records = [record for record in service.list_files(state) if record.input_path.exists()]
        if not records:
            ui.notify("Upload at least one activity file first. Temp inputs are cleaned after conversion.", type="warning")
            return
        profile = selected_profile()

        if convert_button is not None:
            convert_button.disable()
        if zip_button is not None:
            zip_button.disable()
        if progress is not None:
            progress.visible = True
            progress.value = 0
        if progress_label is not None:
            progress_label.text = "0%"
        if status is not None:
            status.text = f"Preparing {profile.label}"

        service.reset_outputs(state)
        records = service.list_files(state)
        refresh_files(touch=False)

        total = len(records)
        for index, record in enumerate(records, start=1):
            service.mark_converting(record.id)
            if status is not None:
                status.text = f"Converting {record.original_name}"
            if progress_label is not None:
                progress_label.text = f"{index - 1}/{total}"
            refresh_files(touch=False)
            await asyncio.sleep(0)
            await nicegui_run.io_bound(
                lambda record_id=record.id: service.convert_record(
                    state,
                    record_id,
                    profile=profile,
                )
            )
            if progress is not None:
                progress.value = index / total
            if progress_label is not None:
                progress_label.text = f"{index}/{total}"
            refresh_files(touch=False)

        await nicegui_run.io_bound(lambda: service.finalize_archive(state, profile=profile))
        state = state_or_notify()
        if state is None:
            return
        summary = service.summarize(state)
        if summary.failed_count:
            ui.notify(f"Converted {summary.converted_count}; failed {summary.failed_count}", type="warning")
        else:
            ui.notify(f"Converted {summary.converted_count} file(s).", type="positive")
        if status is not None:
            status.text = f"Converted {summary.converted_count}; failed {summary.failed_count}"
        refresh_files()

    def download_file(path: Path) -> None:
        if path.exists():
            ui.download(str(path), filename=path.name)
        else:
            ui.notify("That file is no longer available.", type="warning")

    def download_zip() -> None:
        state = state_or_notify()
        if state is None or state.archive_path is None:
            return
        download_file(state.archive_path)

    def clear() -> None:
        state = state_or_notify()
        if state is None:
            return
        service.clear(state)
        if status is not None:
            status.text = "Ready"
        if progress is not None:
            progress.visible = False
            progress.value = 0
        if progress_label is not None:
            progress_label.text = ""
        refresh_files()

    def update_footer() -> None:
        if footer_ip is None or footer_session is None or footer_remaining is None or footer_active is None:
            return
        state = sessions.peek(session_id)
        if state is None:
            footer_ip.text = f"IP {ip_address}"
            footer_session.text = f"Session {session_id[:8]}"
            footer_remaining.text = "Remaining expired"
            footer_active.text = f"Active {sessions.active_count}/{settings.max_sessions}"
            return
        remaining = _format_seconds(state.remaining_seconds(settings.session_timeout_seconds))
        footer_ip.text = f"IP {state.ip_address}"
        footer_session.text = f"Session {state.id[:8]}"
        footer_remaining.text = f"Remaining {remaining}"
        footer_active.text = f"Active {sessions.active_count}/{settings.max_sessions}"

    with ui.header().classes("bg-white text-slate-900 border-b border-slate-200 px-5 py-3 shadow-none"):
        with ui.row().classes("mx-auto w-full max-w-5xl items-center justify-between"):
            ui.image(f"{ASSET_ROUTE}/{LOGO_FILE}").classes("h-11 w-auto max-w-[220px]")
            with ui.row().classes("items-center gap-5 text-sm text-slate-500"):
                ui.label("Converter")
                ui.label("Routes")

    with ui.footer().classes("bg-white text-slate-500 border-t border-slate-200 px-5 py-2 shadow-none"):
        with ui.row().classes("mx-auto w-full max-w-5xl items-center justify-between gap-3 text-xs"):
            with ui.row().classes("items-center gap-4"):
                footer_ip = ui.label()
                footer_session = ui.label()
            with ui.row().classes("items-center gap-4"):
                footer_remaining = ui.label()
                footer_active = ui.label()

    with ui.column().classes("min-h-screen w-full bg-slate-50 px-4 pb-16 pt-8"):
        with ui.column().classes("mx-auto w-full max-w-5xl gap-5"):
            with ui.column().classes("items-center gap-2 text-center"):
                ui.label("Convert workout files").classes("text-4xl font-semibold text-slate-950")
                ui.label("TCX in, import-ready FIT out.").classes("text-base text-slate-500")

            with ui.card().classes("w-full rounded-lg border border-slate-200 p-5 shadow-sm"):
                with ui.row().classes("w-full items-stretch gap-4"):
                    with ui.column().classes("min-w-[240px] flex-1 gap-2"):
                        source_select = ui.select(
                            source_options,
                            value=initial_source,
                            label="From",
                            on_change=lambda _: update_route_info(),
                        ).classes("w-full")
                        source_description = ui.label("").classes("text-xs text-slate-500")
                    with ui.column().classes("min-w-[240px] flex-1 gap-2"):
                        destination_select = ui.select(
                            destination_options,
                            value=initial_destination,
                            label="To",
                            on_change=lambda _: update_route_info(),
                        ).classes("w-full")
                        destination_description = ui.label("").classes("text-xs text-slate-500")

                with ui.column().classes("mt-3 w-full gap-1 rounded-md border border-blue-200 bg-blue-50 p-3"):
                    route_description = ui.label("").classes("text-sm text-blue-950")
                    route_formats = ui.label("").classes("text-xs font-medium text-blue-800")

                ui.separator().classes("my-4")

                ui.upload(
                    label="Upload TCX files",
                    on_upload=handle_upload,
                    on_begin_upload=handle_begin_upload,
                    on_rejected=handle_upload_rejected,
                    multiple=True,
                    auto_upload=True,
                    max_file_size=settings.max_upload_bytes,
                ).props(f"accept={upload_accept}").classes("w-full")
                upload_hint = ui.label("").classes("text-xs text-slate-500")

                with ui.row().classes("mt-4 w-full items-center gap-3"):
                    convert_button = ui.button("Convert", icon="sync", on_click=convert).classes("px-6")
                    zip_button = ui.button("Download ZIP", icon="folder_zip", on_click=download_zip).props(
                        "outline"
                    )
                    ui.button("Clear", icon="delete", on_click=clear).props("flat")
                    convert_button.disable()
                    zip_button.disable()
                    status = ui.label("Ready").classes("ml-auto text-sm text-slate-500")

                with ui.row().classes("mt-3 w-full items-center gap-3"):
                    progress = ui.linear_progress(value=0).classes("flex-1")
                    progress.visible = False
                    progress_label = ui.label("").classes("w-12 text-right text-xs text-slate-500")

            with ui.row().classes("w-full gap-5"):
                with ui.card().classes("min-w-[280px] flex-1 rounded-lg border border-slate-200 p-4 shadow-sm"):
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label("Queue").classes("text-lg font-semibold text-slate-900")
                        ui.icon("pending_actions").classes("text-slate-400")
                    queue_list = ui.column().classes("mt-3 w-full gap-2")

                with ui.card().classes("min-w-[280px] flex-1 rounded-lg border border-slate-200 p-4 shadow-sm"):
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label("Downloads").classes("text-lg font-semibold text-slate-900")
                        ui.icon("download").classes("text-slate-400")
                    output_list = ui.column().classes("mt-3 w-full gap-2")

            with ui.card().classes("w-full rounded-lg border border-slate-200 p-4 shadow-sm"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("Routes").classes("text-lg font-semibold text-slate-900")
                    ui.label(f"{len(supported_profiles())} available").classes("text-xs text-slate-500")
                route_list = ui.row().classes("mt-3 w-full flex-wrap gap-2")

    ui.timer(1.0, update_footer)
    ui.timer(1.0, flush_file_refresh)
    update_route_info()
    refresh_files()


def _file_status_row(record: FileRecord) -> None:
    color = {
        "uploaded": "bg-slate-100 text-slate-700",
        "converting": "bg-blue-100 text-blue-700",
        "converted": "bg-emerald-100 text-emerald-700",
        "failed": "bg-rose-100 text-rose-700",
    }.get(record.status, "bg-slate-100 text-slate-700")
    icon = {
        "uploaded": "description",
        "converting": "sync",
        "converted": "check_circle",
        "failed": "error",
    }.get(record.status, "description")

    with ui.column().classes("w-full gap-1 rounded-md border border-slate-200 bg-white px-3 py-2"):
        with ui.row().classes("w-full items-center justify-between gap-3"):
            with ui.row().classes("min-w-0 items-center gap-2"):
                ui.icon(icon).classes("text-slate-500")
                ui.label(record.original_name).classes("truncate text-sm font-medium text-slate-800")
            ui.label(record.status).classes(f"rounded px-2 py-1 text-xs font-medium {color}")
        ui.label(record.message).classes("text-xs text-slate-500")
        if record.output_paths:
            ui.label(f"{len(record.output_paths)} output file(s), {record.records} records, {record.laps} laps").classes(
                "text-xs text-slate-500"
            )


def _has_cleaned_batch(records: list[FileRecord]) -> bool:
    return any(record.output_paths and not record.input_path.exists() for record in records)


def _client_ip() -> str:
    client = ui.context.client
    request = client.request
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return client.ip or "unknown"


async def _upload_event_payload(event: events.UploadEventArguments) -> tuple[str, bytes]:
    upload_file = getattr(event, "file", None)
    if upload_file is not None:
        return upload_file.name, await upload_file.read()

    content = getattr(event, "content", None)
    filename = str(getattr(event, "name", "activity.tcx"))
    if content is None:
        raise ValueError("Upload event did not include a file payload.")
    if isinstance(content, bytes):
        return filename, content
    if isinstance(content, str):
        return filename, content.encode("utf-8")

    reader = getattr(content, "read", None)
    if not callable(reader):
        raise ValueError("Upload event payload is not readable.")
    data = reader()
    if inspect.isawaitable(data):
        data = await data
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not isinstance(data, bytes):
        raise ValueError("Upload event payload did not return bytes.")
    return filename, data


async def _save_upload_event_to_temp(
    event: events.UploadEventArguments,
    service: WebConversionService,
    state: SessionState,
    profile: ConversionProfile,
) -> tuple[str, Path]:
    upload_file = getattr(event, "file", None)
    if upload_file is not None:
        filename = upload_file.name
        size_getter = getattr(upload_file, "size", None)
        size = size_getter() if callable(size_getter) else None
        target = service.prepare_upload(state, filename, size, profile=profile)
        await upload_file.save(target)
        return filename, target

    filename, content = await _upload_event_payload(event)
    target = service.prepare_upload(state, filename, len(content), profile=profile)
    await nicegui_run.io_bound(target.write_bytes, content)
    return filename, target


def _format_seconds(seconds: int) -> str:
    minutes, remainder = divmod(max(0, seconds), 60)
    return f"{minutes:02d}:{remainder:02d}"


def _add_page_style() -> None:
    ui.add_head_html(
        """
        <style>
          body { margin: 0; background: #f8fafc; }
          .nicegui-content { padding: 0; }
        </style>
        """
    )
