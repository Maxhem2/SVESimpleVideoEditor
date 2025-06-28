import sys
import os
import cv2
import time
import threading
import numpy as np
import subprocess
import re
from imageio_ffmpeg import get_ffmpeg_exe

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QPushButton,
                             QLabel, QSlider, QFileDialog, QGraphicsView,
                             QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem,
                             QVBoxLayout, QHBoxLayout, QFrame, QStyle, QProgressBar, QMessageBox,
                             QGridLayout)
from PyQt5.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QBrush, QIcon
from PyQt5.QtCore import Qt, QTimer, QRectF, QObject, QThread, pyqtSignal
from moviepy.editor import VideoFileClip
import pyaudio

def resource_path(relative_path):
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

STYLESHEET = """
QWidget {
    background-color: #2E2F30;
    color: #E0E0E0;
    font-family: Arial, sans-serif;
    font-size: 11pt;
}
QMainWindow { border: 1px solid #454545; }
QPushButton { background-color: #4A4B4C; border: 1px solid #555555; padding: 8px; border-radius: 4px; }
QPushButton:hover { background-color: #5A5B5C; }
QPushButton:pressed { background-color: #6A6B6C; }
QPushButton:disabled { background-color: #3A3B3C; color: #808080; }
QSlider::groove:horizontal { border: 1px solid #454545; height: 8px; background: #3A3B3C; margin: 2px 0; border-radius: 4px; }
QSlider::handle:horizontal { background: #78797A; border: 1px solid #858585; width: 18px; margin: -5px 0; border-radius: 9px; }
QLabel { color: #E0E0E0; }
QGraphicsView { border: 1px solid #454545; border-radius: 4px; }
QProgressBar { border: 1px solid #454545; border-radius: 4px; text-align: center; color: #E0E0E0; }
QProgressBar::chunk { background-color: #0078D7; border-radius: 4px; }
"""

def detect_ffmpeg_hw_acceleration():
    ffmpeg_path = get_ffmpeg_exe()
    hw_accels = {
        "NVIDIA CUDA": "h264_nvenc",
        "AMD AMF": "h264_amf",
        "Intel QSV": "h264_qsv",
    }
    
    for name, codec in hw_accels.items():
        try:
            cmd = [ffmpeg_path, "-h", f"encoder={codec}"]
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo)
            stdout, stderr = proc.communicate()
            
            if proc.returncode == 0 and b"Unknown encoder" not in stderr:
                print(f"DEBUG: Found supported HW acceleration: {name}")
                return name, codec
        except (FileNotFoundError, Exception) as e:
            print(f"DEBUG: Error checking for {name} support: {e}")
            continue
            
    print("DEBUG: No special HW acceleration found. Using libx264 (CPU).")
    return "CPU", "libx264"

class AudioThread(threading.Thread):
    def __init__(self, audio_clip, video_fps):
        super().__init__()
        self.daemon = True
        self.audio_clip = audio_clip
        self.video_fps = video_fps
        self.p = pyaudio.PyAudio()

        self.frames_per_buffer = 2048

        self.stream = self.p.open(format=pyaudio.paFloat32,
                                  channels=audio_clip.nchannels,
                                  rate=audio_clip.fps,
                                  output=True,
                                  frames_per_buffer=self.frames_per_buffer)
        self.lock = threading.Lock()
        self.is_paused = threading.Event()
        self.stop_event = threading.Event()
        self.is_paused.set()
        self.is_muted = False

        self.seek_request_frame = None
        self.chunk_generator = None

    def run(self):
        chunk_size = self.frames_per_buffer

        while not self.stop_event.is_set():
            with self.lock:
                seek_frame = self.seek_request_frame
                self.seek_request_frame = None

            if seek_frame is not None:
                try:
                    seek_time = seek_frame / self.video_fps if self.video_fps > 0 else 0
                    play_clip = self.audio_clip.subclip(seek_time)
                    self.chunk_generator = play_clip.iter_chunks(chunksize=chunk_size)
                except Exception as e:
                    print(f"DEBUG: Error during audio seek: {e}")
                    self.chunk_generator = None

            if self.is_paused.is_set() or self.chunk_generator is None:
                time.sleep(0.01)
                continue

            try:
                chunk = next(self.chunk_generator)
                samples = np.zeros_like(chunk) if self.is_muted else chunk
                self.stream.write(samples.astype(np.float32).tobytes())
            except StopIteration:
                self.is_paused.set()
                self.chunk_generator = None
            except Exception as e:
                print(f"DEBUG: Audio thread error during playback: {e}")
                self.is_paused.set()

        self.stream.stop_stream()
        self.stream.close()
        self.p.terminate()
        print("DEBUG: Audio thread terminated cleanly.")

    def seek(self, frame):
        with self.lock:
            self.seek_request_frame = frame

    def set_mute(self, muted):
        self.is_muted = muted

    def pause(self):
        self.is_paused.set()

    def resume(self):
        self.is_paused.clear()

    def stop(self):
        self.stop_event.set()

class AudioWaveformWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.waveform_data = None
        self.setMinimumHeight(80)

    def set_waveform_data(self, data):
        self.waveform_data = data
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.waveform_data is None:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#2A2B2C"))
        pen = QPen(QColor(0, 120, 215, 150))
        pen.setWidth(1)
        painter.setPen(pen)

        w, h = self.width(), self.height()
        center_y = h / 2

        if len(self.waveform_data) > 0:
            step = w / len(self.waveform_data)
            for i, amp in enumerate(self.waveform_data):
                x = int(i * step)
                line_height = amp * h * 0.9
                y1, y2 = center_y - line_height / 2, center_y + line_height / 2
                painter.drawLine(int(x), int(y1), int(x), int(y2))

class TimelineSlider(QSlider):
    def __init__(self, orientation):
        super().__init__(orientation)
        self.start_frame = 0
        self.end_frame = 0
        self.total_frames = 0

    def set_markers(self, start, end, total):
        self.start_frame = start
        self.end_frame = end
        self.total_frames = total
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.total_frames > 0:
            painter = QPainter(self)
            w = self.width()
            start_pos = (self.start_frame / self.total_frames) * w
            end_pos = (self.end_frame / self.total_frames) * w

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 120, 215, 90))
            painter.drawRect(int(start_pos), 0, int(end_pos - start_pos), self.height())

            painter.setPen(QPen(QColor(100, 220, 100), 2))
            painter.drawLine(int(start_pos), 0, int(start_pos), self.height())

            painter.setPen(QPen(QColor(220, 100, 100), 2))
            painter.drawLine(int(end_pos), 0, int(end_pos), self.height())

class ClickableSlider(TimelineSlider):
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            value = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), event.x(), self.width())
            self.setValue(value)
            self.sliderMoved.emit(value)
        super().mousePressEvent(event)

class VideoDisplayWidget(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setFrameShape(QFrame.NoFrame)
        self.pixmap_item = QGraphicsPixmapItem()
        self.scene().addItem(self.pixmap_item)
        self.crop_rect_item = None
        self.is_cropping = False
        self.crop_area = None

    def set_frame(self, q_image):
        pixmap = QPixmap.fromImage(q_image)
        self.pixmap_item.setPixmap(pixmap)
        self.scene().setSceneRect(self.pixmap_item.boundingRect())
        self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)

    def start_cropping(self):
        self.is_cropping = True
        self.setCursor(Qt.CrossCursor)
        self.reset_crop()

    def reset_crop(self):
        if self.crop_rect_item:
            self.scene().removeItem(self.crop_rect_item)
            self.crop_rect_item = None
        self.crop_area = None

    def mousePressEvent(self, event):
        if self.is_cropping:
            crop_start_point = self.mapToScene(event.pos())
            if self.crop_rect_item: self.scene().removeItem(self.crop_rect_item)
            pen = QPen(QColor(50, 150, 255), 3, Qt.SolidLine)
            brush = QBrush(QColor(50, 150, 255, 60))
            self.crop_rect_item = QGraphicsRectItem(QRectF(crop_start_point, crop_start_point))
            self.crop_rect_item.setPen(pen)
            self.crop_rect_item.setBrush(brush)
            self.scene().addItem(self.crop_rect_item)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.is_cropping and self.crop_rect_item:
            rect = QRectF(self.crop_rect_item.rect().topLeft(), self.mapToScene(event.pos())).normalized()
            self.crop_rect_item.setRect(rect)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.is_cropping and self.crop_rect_item:
            self.crop_area = self.crop_rect_item.rect()
            print(f"DEBUG: Crop area selected (scene coordinates): {self.crop_area}")
            self.is_cropping = False
            self.setCursor(Qt.ArrowCursor)
        super().mouseReleaseEvent(event)

class LoadVideoWorker(QObject):
    finished = pyqtSignal(dict)
    progress = pyqtSignal(int, str)

    def __init__(self, video_path):
        super().__init__()
        self.video_path = video_path

    def run(self):
        results = {}
        try:
            self.progress.emit(10, "Loading video file...")
            video_capture = cv2.VideoCapture(self.video_path)
            original_clip = VideoFileClip(self.video_path, audio_buffersize=500000)

            if not video_capture.isOpened():
                raise IOError("Cannot open video file.")
            
            results['video_capture'] = video_capture
            results['original_clip'] = original_clip
            results['fps'] = video_capture.get(cv2.CAP_PROP_FPS) or 30
            results['total_frames'] = int(video_capture.get(cv2.CAP_PROP_FRAME_COUNT))
            results['original_width'] = int(video_capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            results['original_height'] = int(video_capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

            self.progress.emit(30, "Analyzing audio...")
            results['has_audio'] = False
            results['waveform_data'] = None
            audio_clip = original_clip.audio
            if audio_clip and audio_clip.duration and audio_clip.max_volume() > 0.001:
                results['has_audio'] = True
                results['audio_clip'] = audio_clip
                self.progress.emit(50, "Generating audio waveform...")
                samples = 1000
                duration = audio_clip.duration
                waveform = []
                if duration and duration > 0:
                    for i in range(samples):
                        subclip = audio_clip.subclip(i * duration / samples, (i + 1) * duration / samples)
                        waveform.append(subclip.max_volume())
                        if i % 10 == 0:
                            self.progress.emit(50 + int((i / samples) * 50), f"Generating waveform ({int(i/samples*100)}%)...")
                    
                    max_amp = max(waveform) if waveform else 1
                    if max_amp > 0: waveform = [w / max_amp for w in waveform]
                    results['waveform_data'] = waveform
                    print("DEBUG: Waveform generation complete.")
            
            self.progress.emit(100, "Load complete!")
            results['error'] = None

        except Exception as e:
            print(f"ERROR: Failed to load video: {e}")
            results['error'] = str(e)
            if 'original_clip' in results and results['original_clip']:
                results['original_clip'].close()
            if 'video_capture' in results and results['video_capture']:
                results['video_capture'].release()
        
        finally:
            self.finished.emit(results)


class SaveWorker(QObject):
    finished = pyqtSignal(str, str)
    progress = pyqtSignal(int)

    def __init__(self, video_path, save_path, start_time, end_time, crop_details, is_muted, video_codec):
        super().__init__()
        self.video_path = video_path
        self.save_path = save_path
        self.start_time = start_time
        self.end_time = end_time
        self.crop_details = crop_details
        self.is_muted = is_muted
        self.video_codec = video_codec
        self.duration = self.end_time - self.start_time

    def run(self):
        error_message = ""
        ffmpeg_path = get_ffmpeg_exe()
        
        cmd = [
            ffmpeg_path, '-y', '-ss', str(self.start_time), '-to', str(self.end_time), '-i', self.video_path,
        ]

        video_filters = []
        if self.crop_details:
            w, h, x, y = self.crop_details['width'], self.crop_details['height'], self.crop_details['x1'], self.crop_details['y1']
            video_filters.append(f"crop={w}:{h}:{x}:{y}")

        if video_filters:
            cmd.extend(['-vf', ",".join(video_filters)])

        cmd.extend(['-c:v', self.video_codec])

        if self.is_muted:
            cmd.append('-an')
        else:
            cmd.extend(['-c:a', 'aac'])

        cmd.append(self.save_path)
        
        print(f"DEBUG: Running FFmpeg command: {' '.join(cmd)}")

        try:
            startupinfo = None
            creationflags = 0
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                creationflags = subprocess.CREATE_NO_WINDOW

            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=startupinfo, creationflags=creationflags)
            
            stderr_lines = []
            time_regex = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")

            for line in proc.stderr:
                stderr_lines.append(line)
                match = time_regex.search(line)
                if match:
                    hours = int(match.group(1))
                    minutes = int(match.group(2))
                    seconds = int(match.group(3))
                    hundredths = int(match.group(4))
                    current_seconds = hours * 3600 + minutes * 60 + seconds + hundredths / 100
                    
                    if self.duration > 0:
                        percent = min(100, int((current_seconds / self.duration) * 100))
                        self.progress.emit(percent)

            return_code = proc.wait()

            if return_code != 0:
                error_message = f"FFmpeg Error (code {return_code}):\n{''.join(stderr_lines)}"
            else:
                self.progress.emit(100)
        except Exception as e:
            error_message = str(e)
        finally:
            self.finished.emit(error_message, self.save_path)

class SaveAudioWorker(QObject):
    finished = pyqtSignal(str, str)
    progress = pyqtSignal(int)

    def __init__(self, video_path, save_path, start_time, end_time):
        super().__init__()
        self.video_path = video_path
        self.save_path = save_path
        self.start_time = start_time
        self.end_time = end_time
        self.duration = self.end_time - self.start_time

    def run(self):
        error_message = ""
        ffmpeg_path = get_ffmpeg_exe()
        
        cmd = [
            ffmpeg_path, '-y', '-ss', str(self.start_time), '-to', str(self.end_time),
            '-i', self.video_path, '-vn', '-c:a', 'libmp3lame', self.save_path,
        ]

        print(f"DEBUG: Running FFmpeg command: {' '.join(cmd)}")
        
        try:
            startupinfo = None
            creationflags = 0
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                creationflags = subprocess.CREATE_NO_WINDOW

            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=startupinfo, creationflags=creationflags)
            
            stderr_lines = []
            time_regex = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")

            for line in proc.stderr:
                stderr_lines.append(line)
                match = time_regex.search(line)
                if match:
                    hours = int(match.group(1))
                    minutes = int(match.group(2))
                    seconds = int(match.group(3))
                    hundredths = int(match.group(4))
                    current_seconds = hours * 3600 + minutes * 60 + seconds + hundredths / 100
                    
                    if self.duration > 0:
                        percent = min(100, int((current_seconds / self.duration) * 100))
                        self.progress.emit(percent)
                        
            return_code = proc.wait()

            if return_code != 0:
                error_message = f"FFmpeg Error (code {return_code}):\n{''.join(stderr_lines)}"
            else:
                self.progress.emit(100)
        except Exception as e:
            error_message = str(e)
        finally:
            self.finished.emit(error_message, self.save_path)

class VideoEditorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SVE Simple Video Editor")
        icon_path = resource_path("icon.ico")
        if os.path.exists(icon_path): self.setWindowIcon(QIcon(icon_path))
        self.setGeometry(100, 100, 1200, 800)
        self.setStyleSheet(STYLESHEET)

        self.video_capture, self.original_clip, self.audio_thread, self.video_path = None, None, None, None
        self.is_playing, self.is_muted, self.has_audio = False, False, False
        self.start_frame, self.end_frame, self.total_frames = 0, -1, 0
        self.fps, self.original_width, self.original_height = 30, 0, 0
        self.ffmpeg_hw_name, self.ffmpeg_codec = "CPU", "libx264"
        
        self.load_thread, self.load_worker = None, None
        self.save_thread, self.save_worker = None, None
        self.save_audio_thread, self.save_audio_worker = None, None

        self.playback_timer = QTimer(self)
        self.playback_timer.setTimerType(Qt.PreciseTimer)
        self.playback_timer.timeout.connect(self.next_frame)
        
        self.setup_ui()
        self.detect_and_display_ffmpeg()
        self.update_ui_state(is_video_loaded=False)

    def setup_ui(self):
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        
        self.video_display = VideoDisplayWidget()
        self.layout.addWidget(self.video_display)

        self.waveform_widget = AudioWaveformWidget()
        self.layout.addWidget(self.waveform_widget)

        self.timeline_slider = ClickableSlider(Qt.Horizontal)
        self.timeline_slider.sliderMoved.connect(self.set_position)
        self.timeline_slider.valueChanged.connect(self.update_time_labels_from_slider)
        self.layout.addWidget(self.timeline_slider)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.layout.addWidget(self.progress_bar)

        self.current_time_label = QLabel("Current: 00:00.000")
        self.start_time_label = QLabel("Start: 00:00.000")
        self.end_time_label = QLabel("End: 00:00.000")
        self.ffmpeg_info_label = QLabel("Using: ...")
        self.ffmpeg_info_label.setAlignment(Qt.AlignCenter)
        
        self.open_button = QPushButton("Open File")
        self.unload_button = QPushButton("Unload Video")
        self.play_pause_button = QPushButton()
        self.play_pause_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.mute_button = QPushButton()
        self.mute_button.setIcon(self.style().standardIcon(QStyle.SP_MediaVolume))
        self.set_start_button = QPushButton("Set Start")
        self.set_end_button = QPushButton("Set End")
        self.crop_button = QPushButton("Crop")
        self.save_button = QPushButton("Save Video")
        self.save_audio_button = QPushButton("Save Audio")
        
        controls_grid = QGridLayout()
        controls_grid.addWidget(self.current_time_label, 0, 0, Qt.AlignLeft)
        controls_grid.addWidget(self.ffmpeg_info_label, 0, 1, Qt.AlignCenter)
        
        time_right_layout = QHBoxLayout()
        time_right_layout.setContentsMargins(0,0,0,0)
        time_right_layout.addWidget(self.start_time_label)
        time_right_layout.addWidget(self.end_time_label)
        controls_grid.addLayout(time_right_layout, 0, 2, Qt.AlignRight)
        
        buttons_left_layout = QHBoxLayout()
        buttons_left_layout.setContentsMargins(0,0,0,0)
        buttons_left_layout.addWidget(self.open_button)
        buttons_left_layout.addWidget(self.unload_button)
        controls_grid.addLayout(buttons_left_layout, 1, 0, Qt.AlignLeft)

        buttons_center_layout = QHBoxLayout()
        buttons_center_layout.setContentsMargins(0,0,0,0)
        buttons_center_layout.addWidget(self.mute_button)
        buttons_center_layout.addWidget(self.play_pause_button)
        buttons_center_layout.addWidget(self.set_start_button)
        buttons_center_layout.addWidget(self.set_end_button)
        buttons_center_layout.addWidget(self.crop_button)
        controls_grid.addLayout(buttons_center_layout, 1, 1, Qt.AlignCenter)

        buttons_right_layout = QHBoxLayout()
        buttons_right_layout.setContentsMargins(0,0,0,0)
        buttons_right_layout.addWidget(self.save_button)
        buttons_right_layout.addWidget(self.save_audio_button)
        controls_grid.addLayout(buttons_right_layout, 1, 2, Qt.AlignRight)

        controls_grid.setColumnStretch(0, 1)
        controls_grid.setColumnStretch(1, 2)
        controls_grid.setColumnStretch(2, 1)

        self.layout.addLayout(controls_grid)

        self.open_button.clicked.connect(self.open_file)
        self.unload_button.clicked.connect(self.unload_video)
        self.play_pause_button.clicked.connect(self.toggle_play_pause)
        self.mute_button.clicked.connect(self.toggle_mute)
        self.set_start_button.clicked.connect(self.set_start_point)
        self.set_end_button.clicked.connect(self.set_end_point)
        self.crop_button.clicked.connect(self.start_cropping_and_pause)
        self.save_button.clicked.connect(self.save_video)
        self.save_audio_button.clicked.connect(self.save_audio_only)

    def detect_and_display_ffmpeg(self):
        self.ffmpeg_hw_name, self.ffmpeg_codec = detect_ffmpeg_hw_acceleration()
        self.ffmpeg_info_label.setText(f"Using: {self.ffmpeg_hw_name}")

    def update_ui_state(self, is_video_loaded, is_processing=False):
        self.open_button.setEnabled(not is_processing)
        self.unload_button.setEnabled(is_video_loaded and not is_processing)
        self.play_pause_button.setEnabled(is_video_loaded and not is_processing)
        self.timeline_slider.setEnabled(is_video_loaded and not is_processing)
        self.set_start_button.setEnabled(is_video_loaded and not is_processing)
        self.set_end_button.setEnabled(is_video_loaded and not is_processing)
        self.crop_button.setEnabled(is_video_loaded and not is_processing)
        self.save_button.setEnabled(is_video_loaded and not is_processing)
        self.save_audio_button.setEnabled(self.has_audio and not is_processing)
        self.mute_button.setEnabled(self.has_audio and not is_processing)
        if is_video_loaded:
            self.timeline_slider.set_markers(self.start_frame, self.end_frame, self.total_frames)

    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Video", "", "Video Files (*.mp4 *.avi *.mkv *.mov)")
        if file_path:
            self.unload_video()
            self.video_path = file_path
            self.load_video()

    def unload_video(self):
        self.cleanup_resources()
        self.video_path = None
        self.video_display.pixmap_item.setPixmap(QPixmap())
        self.waveform_widget.set_waveform_data(None)
        self.start_frame, self.end_frame, self.total_frames = 0, -1, 0
        self.timeline_slider.setRange(0, 0)
        self.timeline_slider.setValue(0)
        self.update_time_labels()
        self.update_ui_state(False)
        self.current_time_label.setText("Current: 00:00.000")
        self.start_time_label.setText("Start: 00:00.000")
        self.end_time_label.setText("End: 00:00.000")

    def load_video(self):
        if not self.video_path: return
        
        self.update_ui_state(is_video_loaded=False, is_processing=True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)

        self.load_thread = QThread()
        self.load_worker = LoadVideoWorker(self.video_path)
        self.load_worker.moveToThread(self.load_thread)

        self.load_worker.progress.connect(self.on_load_progress)
        self.load_worker.finished.connect(self.on_video_load_complete)
        self.load_thread.started.connect(self.load_worker.run)
        self.load_worker.finished.connect(self.load_thread.quit)
        self.load_thread.finished.connect(self.load_thread.deleteLater)
        self.load_worker.finished.connect(self.load_worker.deleteLater)

        self.load_thread.start()
        
    def on_load_progress(self, value, text):
        self.progress_bar.setValue(value)
        self.progress_bar.setFormat(text)

    def on_video_load_complete(self, results):
        self.progress_bar.setVisible(False)

        if results.get('error'):
            self.show_error_message(f"Error loading video file:\n{results['error']}")
            self.unload_video()
            return

        self.video_capture = results['video_capture']
        self.original_clip = results['original_clip']
        self.fps = results['fps']
        self.total_frames = results['total_frames']
        self.original_width = results['original_width']
        self.original_height = results['original_height']
        self.has_audio = results['has_audio']
        
        print(f"DEBUG: Video loaded - FPS: {self.fps}, Total Frames: {self.total_frames}, Res: {self.original_width}x{self.original_height}")

        self.timeline_slider.setRange(0, self.total_frames - 1 if self.total_frames > 0 else 0)
        self.start_frame = 0
        self.end_frame = self.total_frames - 1

        if self.has_audio:
            try:
                self.audio_thread = AudioThread(results['audio_clip'], self.fps)
                self.audio_thread.start()
                self.waveform_widget.set_waveform_data(results['waveform_data'])
            except Exception as e:
                print(f"DEBUG: Could not initialize audio thread: {e}")
                self.has_audio = False
        
        if not self.has_audio:
            self.waveform_widget.set_waveform_data(None)

        self.is_muted = not self.has_audio
        self.mute_button.setIcon(self.style().standardIcon(QStyle.SP_MediaVolumeMuted if self.is_muted else QStyle.SP_MediaVolume))
        
        self.set_position(0)
        self.video_display.reset_crop()
        self.update_ui_state(is_video_loaded=True)

    def toggle_play_pause(self):
        if self.is_playing:
            self.is_playing = False
            self.playback_timer.stop()
            if self.audio_thread: self.audio_thread.pause()
            self.play_pause_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        else:
            current_frame = int(self.video_capture.get(cv2.CAP_PROP_POS_FRAMES))
            if current_frame >= self.end_frame:
                self.set_position(self.start_frame)
                current_frame = self.start_frame

            self.is_playing = True
            self.playback_timer.start(int(1000 / self.fps))
            if self.audio_thread:
                self.audio_thread.seek(current_frame)
                self.audio_thread.resume()
            self.play_pause_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))

    def toggle_mute(self):
        if not self.has_audio: return
        self.is_muted = not self.is_muted
        if self.audio_thread: self.audio_thread.set_mute(self.is_muted)
        icon = QStyle.SP_MediaVolumeMuted if self.is_muted else QStyle.SP_MediaVolume
        self.mute_button.setIcon(self.style().standardIcon(icon))

    def next_frame(self):
        if not self.is_playing: return
        current_frame_pos = int(self.video_capture.get(cv2.CAP_PROP_POS_FRAMES))
        if current_frame_pos > self.end_frame:
            self.toggle_play_pause()
            self.set_position(self.end_frame)
            return

        ret, frame = self.video_capture.read()
        if ret:
            self.display_frame(frame)
            self.timeline_slider.blockSignals(True)
            self.timeline_slider.setValue(current_frame_pos)
            self.timeline_slider.blockSignals(False)
            self.update_time_labels_from_slider(current_frame_pos)
        else:
            self.toggle_play_pause()

    def set_position(self, frame_number):
        if self.video_capture:
            if self.is_playing: self.toggle_play_pause()
            self.video_capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            if self.audio_thread: self.audio_thread.seek(frame_number)
            ret, frame = self.video_capture.read()
            if ret: self.display_frame(frame)
            self.update_time_labels()

    def display_frame(self, frame):
        rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        q_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format_RGB888)
        self.video_display.set_frame(q_image)

    def format_time(self, frame_number):
        if self.fps > 0 and self.total_frames > 0:
            secs = frame_number / self.fps
            mins, s = divmod(secs, 60)
            msecs = (s - int(s)) * 1000
            return f"{int(mins):02d}:{int(s):02d}.{int(msecs):03d}"
        return "00:00.000"

    def update_time_labels(self):
        if self.video_capture:
            current_frame = int(self.video_capture.get(cv2.CAP_PROP_POS_FRAMES))
            self.current_time_label.setText(f"Current: {self.format_time(current_frame)}")
            self.start_time_label.setText(f"Start: {self.format_time(self.start_frame)}")
            self.end_time_label.setText(f"End: {self.format_time(self.end_frame)}")
            self.timeline_slider.set_markers(self.start_frame, self.end_frame, self.total_frames)

    def update_time_labels_from_slider(self, frame_number):
        self.current_time_label.setText(f"Current: {self.format_time(frame_number)}")

    def set_start_point(self):
        if self.video_capture:
            self.start_frame = self.timeline_slider.value()
            if self.start_frame >= self.end_frame: self.end_frame = self.total_frames - 1
            self.update_time_labels()

    def set_end_point(self):
        if self.video_capture:
            self.end_frame = self.timeline_slider.value()
            if self.end_frame <= self.start_frame: self.start_frame = 0
            self.update_time_labels()

    def start_cropping_and_pause(self):
        if self.is_playing: self.toggle_play_pause()
        self.video_display.start_cropping()

    def on_save_progress(self, percent):
        self.progress_bar.setValue(percent)

    def save_video(self):
        if not self.video_path: return
        if self.is_playing: self.toggle_play_pause()

        save_path, _ = QFileDialog.getSaveFileName(self, "Save Video", f"{os.path.splitext(os.path.basename(self.video_path))[0]}_edited.mp4", "MP4 Files (*.mp4)")
        if not save_path: return

        self.update_ui_state(True, is_processing=True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(f"Saving video... %p%")
        self.progress_bar.setVisible(True)

        start_time, end_time = self.start_frame / self.fps, self.end_frame / self.fps

        crop_details = None
        if self.video_display.crop_area:
            pixmap_rect = self.video_display.pixmap_item.sceneBoundingRect()
            crop_rect_scene = self.video_display.crop_area

            if pixmap_rect.width() > 0 and pixmap_rect.height() > 0:
                orig_w, orig_h = self.original_width, self.original_height
                scale_x, scale_y = orig_w / pixmap_rect.width(), orig_h / pixmap_rect.height()

                x1, y1 = (crop_rect_scene.x() - pixmap_rect.x()) * scale_x, (crop_rect_scene.y() - pixmap_rect.y()) * scale_y
                w, h = crop_rect_scene.width() * scale_x, crop_rect_scene.height() * scale_y
                
                x1_int, y1_int = max(0, int(x1)), max(0, int(y1))
                w_int = int(w) - (int(w) % 2)
                h_int = int(h) - (int(h) % 2)
                w_int = min(w_int, orig_w - x1_int)
                h_int = min(h_int, orig_h - y1_int)

                if w_int > 0 and h_int > 0:
                    crop_details = {'x1': x1_int, 'y1': y1_int, 'width': w_int, 'height': h_int}

        self.save_thread = QThread()
        self.save_worker = SaveWorker(self.video_path, save_path, start_time, end_time, crop_details, self.is_muted, self.ffmpeg_codec)
        self.save_worker.moveToThread(self.save_thread)
        self.save_worker.progress.connect(self.on_save_progress)
        self.save_worker.finished.connect(self.on_video_save_complete)
        self.save_thread.started.connect(self.save_worker.run)
        self.save_worker.finished.connect(self.save_thread.quit)
        self.save_thread.finished.connect(self.save_thread.deleteLater)
        self.save_worker.finished.connect(self.save_worker.deleteLater)
        self.save_thread.start()

    def save_audio_only(self):
        if not self.video_path or not self.has_audio: return
        if self.is_playing: self.toggle_play_pause()

        save_path, _ = QFileDialog.getSaveFileName(self, "Save Audio", f"{os.path.splitext(os.path.basename(self.video_path))[0]}_audio.mp3", "MP3 Files (*.mp3)")
        if not save_path: return

        self.update_ui_state(True, is_processing=True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Saving audio... %p%")
        self.progress_bar.setVisible(True)

        start_time, end_time = self.start_frame / self.fps, self.end_frame / self.fps

        self.save_audio_thread = QThread()
        self.save_audio_worker = SaveAudioWorker(self.video_path, save_path, start_time, end_time)
        self.save_audio_worker.moveToThread(self.save_audio_thread)
        self.save_audio_worker.progress.connect(self.on_save_progress)
        self.save_audio_worker.finished.connect(self.on_audio_save_complete)
        self.save_audio_thread.started.connect(self.save_audio_worker.run)
        self.save_audio_worker.finished.connect(self.save_audio_thread.quit)
        self.save_audio_thread.finished.connect(self.save_audio_thread.deleteLater)
        self.save_audio_worker.finished.connect(self.save_audio_worker.deleteLater)
        self.save_audio_thread.start()

    def on_video_save_complete(self, error_message, save_path):
        self.progress_bar.setVisible(False)
        if error_message:
            self.show_error_message(f"Error saving file:\n{error_message}")
            self.update_ui_state(is_video_loaded=True)
        else:
            self.show_success_message(f"Video successfully saved to:\n{save_path}")
            print("DEBUG: Video writing complete. Reloading.")
            self.unload_video()
            self.video_path = save_path
            self.load_video()

    def on_audio_save_complete(self, error_message, save_path):
        self.progress_bar.setVisible(False)
        self.update_ui_state(is_video_loaded=True)
        if error_message:
            self.show_error_message(f"Error saving audio file:\n{error_message}")
        else:
            self.show_success_message(f"Audio successfully saved to:\n{save_path}")

    def cleanup_resources(self):
        print("DEBUG: Cleaning up resources...")
        if self.is_playing: self.toggle_play_pause()
        if self.playback_timer.isActive(): self.playback_timer.stop()
        if self.video_capture: self.video_capture.release()
        if self.audio_thread:
            self.audio_thread.stop()
            self.audio_thread.join()
        if self.original_clip: self.original_clip.close()
        
        self.video_display.reset_crop()
        
        self.video_capture, self.original_clip, self.audio_thread, self.has_audio = None, None, None, False
        print("DEBUG: Resources cleaned.")

    def show_error_message(self, message):
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Critical)
        msg_box.setText(message)
        msg_box.setWindowTitle("Error")
        msg_box.exec_()

    def show_success_message(self, message):
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Information)
        msg_box.setText(message)
        msg_box.setWindowTitle("Success")
        msg_box.exec_()

    def closeEvent(self, event):
        self.cleanup_resources()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = VideoEditorWindow()
    window.show()
    sys.exit(app.exec_())
