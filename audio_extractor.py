#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QProgressDialog, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

CONFIG_PATH = os.path.expanduser("~/.config/audio-extractor.json")
VIDEO_FILTER = (
    "Video files (*.mkv *.mp4 *.m4v *.avi *.mov *.webm *.ts *.m2ts *.mts "
    "*.wmv *.flv *.mpg *.mpeg *.vob *.ogv *.3gp *.divx);;All files (*)"
)

# Original keeps the source codec in a container that fits it.
ORIGINAL_CONTAINERS = {
    "aac": ("m4a", "ipod"),
    "alac": ("m4a", "ipod"),
    "mp3": ("mp3", "mp3"),
    "opus": ("opus", "opus"),
    "vorbis": ("ogg", "ogg"),
    "flac": ("flac", "flac"),
    "ac3": ("ac3", "ac3"),
    "eac3": ("eac3", "eac3"),
    "dts": ("dts", "dts"),
    "truehd": ("thd", "truehd"),
}

CODEC_LABELS = {
    "aac": "AAC",
    "ac3": "AC-3",
    "eac3": "E-AC-3",
    "truehd": "TrueHD",
    "dts": "DTS",
    "flac": "FLAC",
    "opus": "Opus",
    "vorbis": "Vorbis",
    "mp3": "MP3",
    "alac": "ALAC",
}

PROGRESS_KEYS = {
    "frame", "fps", "stream_0_0_q", "bitrate", "total_size", "out_time_us",
    "out_time_ms", "out_time", "dup_frames", "drop_frames", "speed",
    "progress",
}


def load_config():
    cfg = {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("last_dir"), str):
            cfg["last_dir"] = data["last_dir"]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"Failed to save config: {e}", file=sys.stderr)


def probe_media(path):
    """Return {"duration": float|None, "tracks": [...]} for a media file."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries",
        "stream=index,codec_name,channels,channel_layout,bit_rate,"
        "sample_rate,duration:stream_tags=language,title:format=duration",
        "-of", "json",
        path,
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except FileNotFoundError:
        raise RuntimeError(
            "ffprobe not found; install ffmpeg on this machine"
        ) from None
    except subprocess.CalledProcessError as e:
        detail = (e.output or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffprobe failed: {detail[:300]}") from None

    try:
        data = json.loads(out.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RuntimeError("ffprobe returned invalid data") from None

    tracks = []
    durations = []
    for stream in data.get("streams", []):
        tags = stream.get("tags") or {}
        try:
            bitrate = int(stream.get("bit_rate") or 0) or None
        except (TypeError, ValueError):
            bitrate = None
        try:
            channels = int(stream.get("channels") or 0)
        except (TypeError, ValueError):
            channels = 0
        try:
            sample_rate = int(stream.get("sample_rate") or 0) or None
        except (TypeError, ValueError):
            sample_rate = None
        try:
            stream_duration = float(stream.get("duration") or 0) or None
        except (TypeError, ValueError):
            stream_duration = None
        if stream_duration:
            durations.append(stream_duration)
        tracks.append({
            "index": stream.get("index"),
            "codec": stream.get("codec_name") or "",
            "channels": channels,
            "channel_layout": stream.get("channel_layout") or "",
            "bitrate": bitrate,
            "sample_rate": sample_rate,
            "language": tags.get("language") or "",
            "title": tags.get("title") or "",
        })

    duration = None
    try:
        duration = float((data.get("format") or {}).get("duration") or 0) or None
    except (TypeError, ValueError):
        duration = None
    if duration is None and durations:
        duration = max(durations)
    return {"duration": duration, "tracks": tracks}


def track_name(track, position):
    title = (track.get("title") or "").strip()
    language = (track.get("language") or "").strip()
    if title and language:
        return f"{title} ({language})"
    return title or language or f"Track {position}"


def codec_label(track):
    codec = (track.get("codec") or "").lower()
    if codec.startswith("pcm_"):
        return "PCM"
    return CODEC_LABELS.get(codec, codec.upper() or "?")


def channels_text(track):
    channels = track.get("channels") or 0
    layout = (track.get("channel_layout") or "").strip()
    if channel_layout_is_surround(layout):
        return f"{channels} ({layout})"
    if channels == 1:
        return "1 (mono)"
    if channels == 2:
        return "2 (stereo)"
    if channels:
        return str(channels)
    return "?"


def channel_layout_is_surround(layout):
    return bool(layout) and not layout.startswith(("mono", "stereo"))


def format_bitrate(bitrate):
    if not bitrate:
        return None
    if bitrate >= 1_000_000:
        return f"{bitrate / 1_000_000:.1f} Mb/s".replace(".0 ", " ")
    return f"{round(bitrate / 1000)} kb/s"


def quality_text(track):
    parts = [codec_label(track)]
    bitrate = format_bitrate(track.get("bitrate"))
    if bitrate:
        parts.append(bitrate)
    sample_rate = track.get("sample_rate")
    if sample_rate:
        parts.append(f"{sample_rate / 1000:g} kHz")
    return " · ".join(parts)


def format_duration(seconds):
    if not seconds:
        return ""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def output_container(track, format_choice):
    """Return (extension, ffmpeg muxer or None) for the chosen format."""
    if format_choice == "mka":
        return "mka", "matroska"
    if format_choice == "mp3":
        return "mp3", "mp3"
    if format_choice == "opus":
        return "opus", "opus"
    codec = (track.get("codec") or "").lower()
    if codec.startswith("pcm_"):
        return "wav", "wav"
    extension, muxer = ORIGINAL_CONTAINERS.get(codec, ("mka", "matroska"))
    return extension, muxer


def target_bitrate(track, maximum):
    """Match the source bitrate where possible, capped by the codec maximum."""
    source = track.get("bitrate") or 0
    if source > 0:
        return max(64, min(round(source / 1000), maximum))
    return maximum


def sanitize_filename(name, max_length=100):
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if len(name) > max_length:
        name = name[:max_length].rstrip(" .")
    return name or "audio"


def output_filename(stem, position, track, extension):
    parts = [stem, f"- a{position}"]
    language = (track.get("language") or "").strip()
    title = (track.get("title") or "").strip()
    if language:
        parts.append(language)
    if title:
        parts.append(title)
    return f"{sanitize_filename(' '.join(parts))}.{extension}"


def unique_path(path, taken):
    base, extension = os.path.splitext(path)
    candidate = path
    number = 2
    while candidate in taken or os.path.exists(candidate):
        candidate = f"{base} ({number}){extension}"
        number += 1
    taken.add(candidate)
    return candidate


def build_command(input_path, track, format_choice, output_path,
                  ffmpeg="ffmpeg"):
    extension, muxer = output_container(track, format_choice)
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-i", input_path,
        "-map", f"0:{track['index']}",
        "-vn", "-sn", "-dn",
    ]
    if format_choice in ("original", "mka"):
        cmd += ["-c:a", "copy"]
    elif format_choice == "mp3":
        cmd += [
            "-c:a", "libmp3lame",
            "-b:a", f"{target_bitrate(track, 320)}k",
        ]
        if (track.get("channels") or 0) > 2:
            cmd += ["-ac", "2"]
    elif format_choice == "opus":
        cmd += [
            "-c:a", "libopus",
            "-b:a", f"{target_bitrate(track, 256)}k",
        ]
        channels = track.get("channels") or 0
        if channels == 6:
            cmd += ["-af", "aformat=channel_layouts=5.1"]
        elif channels == 8:
            cmd += ["-af", "aformat=channel_layouts=7.1"]
        elif channels > 2:
            cmd += ["-ac", "2"]
    if muxer:
        cmd += ["-f", muxer]
    cmd += ["-progress", "pipe:1", "-nostats", output_path]
    return cmd


def make_jobs(input_path, selected, format_choice, output_dir):
    """Build extraction jobs for (position, track) pairs."""
    stem = sanitize_filename(os.path.splitext(os.path.basename(input_path))[0])
    taken = set()
    jobs = []
    for position, track in selected:
        extension, _muxer = output_container(track, format_choice)
        filename = output_filename(stem, position, track, extension)
        output_path = unique_path(os.path.join(output_dir, filename), taken)
        jobs.append({
            "track": track,
            "output": output_path,
            "cmd": build_command(
                input_path, track, format_choice, output_path
            ),
        })
    return jobs


class ExtractionCancelled(Exception):
    pass


class ExtractWorker(QThread):
    progress = pyqtSignal(int, str)
    done = pyqtSignal(bool, str)

    def __init__(self, jobs, duration, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.duration = duration or 0
        self.outputs = []
        self.error = ""
        self.cancelled = False
        self._cancel_requested = False
        self._proc = None

    def cancel(self):
        self._cancel_requested = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def run(self):
        try:
            total = len(self.jobs)
            for index, job in enumerate(self.jobs):
                if self._cancel_requested:
                    raise ExtractionCancelled()
                label = (
                    f"Extracting {index + 1} of {total}: "
                    f"{os.path.basename(job['output'])}"
                )
                self.progress.emit(int(index * 100 / total), label)
                self._run_ffmpeg(job, index, total, label)
                self.outputs.append(job["output"])
            self.progress.emit(100, "Finished")
            self.done.emit(True, "")
        except ExtractionCancelled:
            self.cancelled = True
            self.done.emit(False, "Cancelled")
        except Exception as e:  # noqa: BLE001 - report any failure to the GUI
            self.error = str(e)
            self.done.emit(False, self.error)

    def _run_ffmpeg(self, job, index, total, label):
        log = []
        try:
            self._proc = subprocess.Popen(
                job["cmd"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "ffmpeg not found; install ffmpeg on this machine"
            ) from None

        duration = self.duration
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            key, _sep, value = line.partition("=")
            if key in ("out_time_us", "out_time_ms"):
                if duration > 0:
                    try:
                        micros = int(value)
                    except ValueError:
                        continue
                    if micros > 0:
                        fraction = min(1.0, micros / (duration * 1_000_000))
                        percent = int((index + fraction) * 100 / total)
                        self.progress.emit(min(99, percent), label)
                continue
            if key in PROGRESS_KEYS or (
                key.startswith("stream_") and key.endswith("_q")
            ):
                continue
            log.append(line)

        return_code = self._proc.wait()
        self._proc = None
        if self._cancel_requested:
            self._discard_output(job)
            raise ExtractionCancelled()
        if return_code != 0:
            self._discard_output(job)
            detail = "\n".join(log[-8:]) or f"exit code {return_code}"
            raise RuntimeError(
                f"ffmpeg failed for {os.path.basename(job['output'])}:\n"
                f"{detail}"
            )
        if not os.path.exists(job["output"]):
            raise RuntimeError(
                f"ffmpeg produced no output for {job['output']}"
            )

    @staticmethod
    def _discard_output(job):
        try:
            os.remove(job["output"])
        except OSError:
            pass


class FileDropLineEdit(QLineEdit):
    fileDropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if self._first_local_file(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._first_local_file(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        path = self._first_local_file(event.mimeData())
        if path:
            event.acceptProposedAction()
            self.fileDropped.emit(path)
        else:
            super().dropEvent(event)

    @staticmethod
    def _first_local_file(mime_data):
        if not mime_data.hasUrls():
            return None
        for url in mime_data.urls():
            if url.isLocalFile():
                return url.toLocalFile()
        return None


class AudioExtractorDialog(QDialog):
    def __init__(self, video_path=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Audio Extractor")
        self.setWindowIcon(QIcon.fromTheme("audio-x-generic"))
        self.resize(760, 480)
        self._tracks = []
        self._duration = None
        self._loaded_path = None
        self._worker = None
        self._progress = None

        layout = QVBoxLayout(self)

        path_layout = QHBoxLayout()
        self.path_edit = FileDropLineEdit()
        self.path_edit.setPlaceholderText(
            "Drop a video file here or type a path…"
        )
        self.path_edit.setClearButtonEnabled(True)
        self.path_edit.returnPressed.connect(self._on_path_edited)
        self.path_edit.editingFinished.connect(self._on_path_edited)
        self.path_edit.fileDropped.connect(self._set_video)
        path_layout.addWidget(self.path_edit, 1)

        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self._on_browse)
        path_layout.addWidget(self.browse_btn)
        layout.addLayout(path_layout)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: gray;")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["", "Track", "Channels", "Quality"]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(0, 32)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Format:"))
        self.format_combo = QComboBox()
        for text, data in [
            ("Original", "original"),
            ("MP3", "mp3"),
            ("Opus", "opus"),
            ("MKA", "mka"),
        ]:
            self.format_combo.addItem(text, data)
        self.format_combo.setToolTip(
            "Original and MKA copy the source codec without quality loss. "
            "MP3 downmixes multichannel tracks to stereo."
        )
        bottom.addWidget(self.format_combo)
        bottom.addStretch(1)
        self.extract_btn = QPushButton("EXTRACT")
        self.extract_btn.setMinimumHeight(32)
        self.extract_btn.setMinimumWidth(140)
        self.extract_btn.setStyleSheet("font-weight: bold;")
        self.extract_btn.clicked.connect(self._on_extract)
        bottom.addWidget(self.extract_btn)
        layout.addLayout(bottom)

        if video_path:
            self._set_video(video_path)

    def _set_video(self, path):
        path = os.path.abspath(
            os.path.expanduser(path.strip().strip('"').strip("'"))
        )
        self.path_edit.setText(path)
        self._load_video(path)

    def _on_path_edited(self):
        text = self.path_edit.text().strip()
        if not text:
            self._clear_tracks()
            self._set_status("")
            return
        path = os.path.abspath(os.path.expanduser(text.strip('"').strip("'")))
        if path == self._loaded_path:
            return
        self._load_video(path)

    def _on_browse(self):
        start_dir = ""
        current = self.path_edit.text().strip()
        if current:
            start_dir = os.path.dirname(
                os.path.abspath(os.path.expanduser(current))
            )
        if not os.path.isdir(start_dir):
            start_dir = (
                os.path.dirname(self._loaded_path or "")
                or os.path.expanduser("~")
            )
        path, _filter = QFileDialog.getOpenFileName(
            self, "Select video file", start_dir, VIDEO_FILTER
        )
        if path:
            self._set_video(path)

    def _load_video(self, path):
        self._clear_tracks()
        if not os.path.isfile(path):
            self._loaded_path = None
            self._set_status("File not found", error=True)
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        error = None
        media = None
        try:
            media = probe_media(path)
        except RuntimeError as e:
            error = e
        finally:
            QApplication.restoreOverrideCursor()
        if error is not None:
            self._loaded_path = None
            self._set_status(str(error), error=True)
            QMessageBox.warning(
                self, "Cannot read file", f"{path}:\n{error}"
            )
            return

        self._loaded_path = path
        self._duration = media.get("duration")
        self._tracks = media.get("tracks", [])
        self._populate_tracks()

        if not self._tracks:
            self._set_status("No audio tracks found", error=True)
            return
        count = len(self._tracks)
        suffix = (
            f" · {format_duration(self._duration)}" if self._duration else ""
        )
        plural = "s" if count != 1 else ""
        self._set_status(f"{count} audio track{plural}{suffix}")

    def _populate_tracks(self):
        self.table.setRowCount(0)
        for row, track in enumerate(self._tracks):
            self.table.insertRow(row)

            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable
            )
            check.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, check)

            for column, text in (
                (1, track_name(track, row + 1)),
                (2, channels_text(track)),
                (3, quality_text(track)),
            ):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.table.setItem(row, column, item)

    def _clear_tracks(self):
        self.table.setRowCount(0)
        self._tracks = []
        self._duration = None

    def _set_status(self, text, error=False):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            "color: #c0392b;" if error else "color: gray;"
        )

    def _selected_tracks(self):
        selected = []
        for row, track in enumerate(self._tracks):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.Checked:
                selected.append((row + 1, track))
        return selected

    def _on_extract(self):
        if not self._loaded_path:
            QMessageBox.warning(
                self, "No video", "Select a video file first."
            )
            return
        selected = self._selected_tracks()
        if not selected:
            QMessageBox.warning(
                self, "Nothing selected",
                "Select at least one audio track to extract."
            )
            return

        last_dir = (
            load_config().get("last_dir")
            or os.path.dirname(self._loaded_path)
        )
        if not os.path.isdir(last_dir):
            last_dir = (
                os.path.dirname(self._loaded_path)
                or os.path.expanduser("~")
            )
        output_dir = QFileDialog.getExistingDirectory(
            self, "Select output folder", last_dir
        )
        if not output_dir:
            return
        save_config({"last_dir": output_dir})

        format_choice = self.format_combo.currentData()
        jobs = make_jobs(
            self._loaded_path, selected, format_choice, output_dir
        )
        self._start_worker(jobs)

    def _start_worker(self, jobs):
        worker = ExtractWorker(jobs, self._duration, self)
        self._worker = worker

        progress = QProgressDialog(
            f"Extracting {len(jobs)} track{'s' if len(jobs) != 1 else ''}…",
            "Cancel", 0, 100, self,
        )
        progress.setWindowTitle("Extracting audio")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.canceled.connect(worker.cancel)
        self._progress = progress

        worker.progress.connect(self._on_progress)
        worker.done.connect(self._on_extract_done)
        self._set_busy(True)
        progress.show()
        worker.start()

    def _on_progress(self, percent, label):
        if self._progress is None:
            return
        self._progress.setValue(max(0, min(100, int(percent))))
        self._progress.setLabelText(label)

    def _on_extract_done(self, ok, message):
        worker = self._worker
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        self._set_busy(False)
        if worker is not None:
            worker.wait(2000)
        self._worker = None

        if worker is not None and worker.cancelled:
            self._set_status("Extraction cancelled")
            return
        if not ok:
            self._set_status("Extraction failed", error=True)
            QMessageBox.warning(
                self, "Extraction failed", message or "Unknown error"
            )
            return

        outputs = worker.outputs if worker is not None else []
        folder = os.path.dirname(outputs[0]) if outputs else ""
        self._set_status(
            f"Saved {len(outputs)} file{'s' if len(outputs) != 1 else ''}"
        )
        names = "\n".join(os.path.basename(path) for path in outputs)
        QMessageBox.information(
            self, "Extraction complete",
            f"Saved to {folder}:\n\n{names}",
        )

    def _set_busy(self, busy):
        for widget in (
            self.path_edit, self.browse_btn, self.table,
            self.format_combo, self.extract_btn,
        ):
            widget.setEnabled(not busy)

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Audio Extractor")
    video_path = sys.argv[1] if len(sys.argv) > 1 else None
    dialog = AudioExtractorDialog(video_path)
    dialog.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
