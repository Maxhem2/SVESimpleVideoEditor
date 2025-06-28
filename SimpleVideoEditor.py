import sys
import os
import cv2
import time
import threading
import numpy as np

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QPushButton,
                             QLabel, QSlider, QFileDialog, QGraphicsView,
                             QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem,
                             QVBoxLayout, QHBoxLayout, QFrame, QStyle, QProgressBar, QMessageBox)
from PyQt5.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QBrush, QIcon
from PyQt5.QtCore import Qt, QTimer, QRectF, QObject, QThread, pyqtSignal
from moviepy.editor import VideoFileClip
from moviepy.video.fx.all import crop
import pyaudio

def resource_path(relative_path):
    try:
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
            # Atomically check for and clear any pending seek request
            with self.lock:
                seek_frame = self.seek_request_frame
                self.seek_request_frame = None

            if seek_frame is not None:
                try:
                    seek_time = seek_frame / self.video_fps if self.video_fps > 0 else 0
                    print(f"DEBUG: Audio thread seeking to frame {seek_frame} ({seek_time:.2f}s)")
                    # The subclip operation can be slow, but we only do it on an explicit seek,
                    # not in the hot loop of playback.
                    play_clip = self.audio_clip.subclip(seek_time)
                    self.chunk_generator = play_clip.iter_chunks(chunksize=chunk_size)
                except Exception as e:
                    print(f"DEBUG: Error during audio seek: {e}")
                    self.chunk_generator = None

            if self.is_paused.is_set() or self.chunk_generator is None:
                time.sleep(0.01)
                continue

            try:
                # Get the next chunk of audio samples from the generator
                chunk = next(self.chunk_generator)

                if self.is_muted:
                    samples = np.zeros_like(chunk)
                else:
                    samples = chunk

                # Write the audio data to the stream. This call blocks until the data
                # is processed, naturally pacing the thread.
                self.stream.write(samples.astype(np.float32).tobytes())

            except StopIteration:
                self.is_paused.set()  # End of the (sub)clip
                self.chunk_generator = None  # Mark as finished to allow restart
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

        w = self.width()
        h = self.height()
        center_y = h / 2

        if len(self.waveform_data) > 0:
            step = w / len(self.waveform_data)
            for i, amp in enumerate(self.waveform_data):
                x = int(i * step)
                line_height = amp * h * 0.9

                y1 = center_y - line_height / 2
                y2 = center_y + line_height / 2

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

class SaveWorker(QObject):
    finished = pyqtSignal(str, str)

    def __init__(self, video_path, save_path, start_time, end_time, crop_details, is_muted):
        super().__init__()
        self.video_path = video_path
        self.save_path = save_path
        self.start_time = start_time
        self.end_time = end_time
        self.crop_details = crop_details
        self.is_muted = is_muted

    def run(self):
        error_message = ""
        clip_to_process = None
        subclip = None
        try:
            clip_to_process = VideoFileClip(self.video_path)
            subclip = clip_to_process.subclip(self.start_time, self.end_time)
            if self.crop_details:
                subclip = crop(subclip, **self.crop_details)
            if self.is_muted:
                subclip = subclip.without_audio()
            subclip.write_videofile(self.save_path, codec='libx264', audio_codec='aac', logger=None)
        except Exception as e:
            error_message = str(e)
        finally:
            if subclip: subclip.close()
            if clip_to_process: clip_to_process.close()
            self.finished.emit(error_message, self.save_path)

class SaveAudioWorker(QObject):
    finished = pyqtSignal(str)

    def __init__(self, video_path, save_path, start_time, end_time):
        super().__init__()
        self.video_path = video_path
        self.save_path = save_path
        self.start_time = start_time
        self.end_time = end_time

    def run(self):
        error_message = ""
        clip_to_process = None
        audio_subclip = None
        try:
            clip_to_process = VideoFileClip(self.video_path)
            if not clip_to_process.audio:
                raise ValueError("The selected video has no audio track.")
            audio_subclip = clip_to_process.audio.subclip(self.start_time, self.end_time)
            audio_subclip.write_audiofile(self.save_path, logger=None)
        except Exception as e:
            error_message = str(e)
        finally:
            if audio_subclip: audio_subclip.close()
            if clip_to_process: clip_to_process.close()
            self.finished.emit(error_message)


class VideoEditorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SVE Simple Video Editor")

        icon_path = resource_path("icon.ico")
        self.setWindowIcon(QIcon(icon_path))

        self.setGeometry(100, 100, 1200, 800)
        self.setStyleSheet(STYLESHEET)

        self.video_capture = None
        self.original_clip = None
        self.audio_thread = None
        self.has_audio = False
        self.video_path = None
        self.is_playing = False
        self.is_muted = False
        self.start_frame = 0
        self.end_frame = -1
        self.total_frames = 0
        self.fps = 30
        self.save_thread = None
        self.save_worker = None

        self.playback_timer = QTimer(self)
        self.playback_timer.timeout.connect(self.next_frame)
        self.setup_ui()
        self.update_ui_state(False)

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

        time_layout = QHBoxLayout()
        self.current_time_label = QLabel("Current: 00:00.000")
        self.start_time_label = QLabel("Start: 00:00.000")
        self.end_time_label = QLabel("End: 00:00.000")
        time_layout.addWidget(self.current_time_label)
        time_layout.addStretch()
        time_layout.addWidget(self.start_time_label)
        time_layout.addWidget(self.end_time_label)
        self.layout.addLayout(time_layout)

        controls_layout = QHBoxLayout()
        self.open_button = QPushButton("Open File")
        self.play_pause_button = QPushButton()
        self.mute_button = QPushButton()
        self.set_start_button = QPushButton("Set Start")
        self.set_end_button = QPushButton("Set End")
        self.crop_button = QPushButton("Crop")
        self.save_button = QPushButton("Save Video")
        self.save_audio_button = QPushButton("Save Audio")

        self.play_pause_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.mute_button.setIcon(self.style().standardIcon(QStyle.SP_MediaVolume))

        buttons = [self.open_button, self.mute_button, self.play_pause_button, self.set_start_button,
                   self.set_end_button, self.crop_button, self.save_button, self.save_audio_button]
        controls_layout.addWidget(buttons[0])
        controls_layout.addStretch()
        for btn in buttons[1:-2]: controls_layout.addWidget(btn)
        controls_layout.addStretch()
        controls_layout.addWidget(buttons[-2])
        controls_layout.addWidget(buttons[-1])

        self.open_button.clicked.connect(self.open_file)
        self.play_pause_button.clicked.connect(self.toggle_play_pause)
        self.mute_button.clicked.connect(self.toggle_mute)
        self.set_start_button.clicked.connect(self.set_start_point)
        self.set_end_button.clicked.connect(self.set_end_point)
        self.crop_button.clicked.connect(self.video_display.start_cropping)
        self.save_button.clicked.connect(self.save_video)
        self.save_audio_button.clicked.connect(self.save_audio_only)

        self.layout.addLayout(controls_layout)

    def update_ui_state(self, is_video_loaded, is_saving=False):
        self.open_button.setEnabled(not is_saving)
        self.play_pause_button.setEnabled(is_video_loaded and not is_saving)
        self.timeline_slider.setEnabled(is_video_loaded and not is_saving)
        self.set_start_button.setEnabled(is_video_loaded and not is_saving)
        self.set_end_button.setEnabled(is_video_loaded and not is_saving)
        self.crop_button.setEnabled(is_video_loaded and not is_saving)
        self.save_button.setEnabled(is_video_loaded and not is_saving)
        self.save_audio_button.setEnabled(self.has_audio and not is_saving)
        self.mute_button.setEnabled(self.has_audio and not is_saving)
        self.timeline_slider.set_markers(self.start_frame, self.end_frame, self.total_frames)

    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Video", "", "Video Files (*.mp4 *.avi *.mkv *.mov)")
        if file_path:
            self.video_path = file_path
            self.load_video()

    def load_video(self):
        if not self.video_path: return
        self.cleanup_resources()
        print(f"DEBUG: Loading video: {self.video_path}")

        try:
            self.video_capture = cv2.VideoCapture(self.video_path)
            self.original_clip = VideoFileClip(self.video_path) if VideoFileClip else None
        except Exception as e:
            self.show_error_message(f"Error loading video file: {e}")
            self.cleanup_resources()
            return

        self.fps = self.video_capture.get(cv2.CAP_PROP_FPS) or 30
        self.total_frames = int(self.video_capture.get(cv2.CAP_PROP_FRAME_COUNT))
        print(f"DEBUG: Video properties - FPS: {self.fps}, Total Frames: {self.total_frames}")

        self.timeline_slider.setRange(0, self.total_frames - 1 if self.total_frames > 0 else 0)
        self.start_frame = 0
        self.end_frame = self.total_frames -1

        self.has_audio = False
        if self.original_clip and self.original_clip.audio and pyaudio:
            print("DEBUG: Audio track found. Initializing audio thread...")
            try:
                # Check if audio has a valid duration and is not silent
                if self.original_clip.audio.duration and self.original_clip.audio.max_volume() > 0.001:
                    print("DEBUG: Audio track has sound.")
                    self.audio_thread = AudioThread(self.original_clip.audio, self.fps)
                    self.audio_thread.start()
                    self.has_audio = True
                    self.generate_waveform_data(self.original_clip.audio)
                else:
                    print("DEBUG: Audio track is silent or has zero duration.")
            except Exception as e:
                print(f"DEBUG: Could not initialize audio thread or analyze audio: {e}")
        else:
            print("DEBUG: No audio track found or PyAudio not available.")

        if not self.has_audio:
            self.is_muted = True
            self.mute_button.setIcon(self.style().standardIcon(QStyle.SP_MediaVolumeMuted))
            self.waveform_widget.set_waveform_data(None)
        else:
            self.is_muted = False
            self.mute_button.setIcon(self.style().standardIcon(QStyle.SP_MediaVolume))

        self.set_position(0)
        self.save_button.setText("Save Video")
        self.update_ui_state(True)

    def generate_waveform_data(self, audio_clip, samples=1000):
        try:
            print("DEBUG: Generating waveform...")
            duration = audio_clip.duration
            if duration is None or duration <= 0:
                print("DEBUG: No duration to generate waveform from.")
                self.waveform_widget.set_waveform_data(None)
                return

            step = duration / samples
            waveform = []
            for i in range(samples):
                start = i * step
                end = start + step
                if end > duration:
                    end = duration
                if start >= end:
                    continue

                subclip = audio_clip.subclip(start, end)
                max_vol = subclip.max_volume()
                waveform.append(max_vol)

            max_amp = max(waveform) if waveform else 0
            if max_amp > 0:
                waveform = [w / max_amp for w in waveform]

            self.waveform_widget.set_waveform_data(waveform)
            print("DEBUG: Waveform generation complete.")
        except Exception as e:
            print(f"DEBUG: Failed to generate waveform: {e}")
            self.waveform_widget.set_waveform_data(None)

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
                # Seek to the current frame and then resume playback
                self.audio_thread.seek(current_frame)
                self.audio_thread.resume()

            self.play_pause_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))

    def toggle_mute(self):
        if not self.has_audio:
            return

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
            self.timeline_slider.setValue(current_frame_pos)
            # DO NOT seek audio here. Audio plays independently and was synced
            # at the start of playback. Seeking here causes noise.
        else:
            self.toggle_play_pause()

    def set_position(self, frame_number):
        if self.video_capture:
            self.video_capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            if self.audio_thread:
                # When scrubbing, tell the audio thread where to go.
                # If playing, it will jump. If paused, it will be ready for the next play.
                self.audio_thread.seek(frame_number)
            ret, frame = self.video_capture.read()
            if ret: self.display_frame(frame)
            self.update_time_labels()

    def display_frame(self, frame):
        rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        q_image = QImage(rgb_image.data, w, h, ch * w, QImage.Format_RGB888)
        self.video_display.set_frame(q_image)

    def format_time(self, frame_number):
        secs = frame_number / self.fps if self.fps > 0 else 0
        mins, s = divmod(secs, 60)
        msecs = (s - int(s)) * 1000
        return f"{int(mins):02d}:{int(s):02d}.{int(msecs):03d}"

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
            print(f"DEBUG: New start frame set to: {self.start_frame}")
            if self.start_frame >= self.end_frame: self.end_frame = self.total_frames - 1
            self.update_time_labels()

    def set_end_point(self):
        if self.video_capture:
            self.end_frame = self.timeline_slider.value()
            print(f"DEBUG: New end frame set to: {self.end_frame}")
            if self.end_frame <= self.start_frame: self.start_frame = 0
            self.update_time_labels()

    def save_video(self):
        if not self.video_path: return
        save_path, _ = QFileDialog.getSaveFileName(self, "Save Video", f"{os.path.splitext(self.video_path)[0]}_edited.mp4", "MP4 Files (*.mp4)")
        if not save_path: return

        self.update_ui_state(True, is_saving=True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("Saving video... this may take a moment.")
        self.progress_bar.setVisible(True)

        start_time = self.start_frame / self.fps
        end_time = self.end_frame / self.fps

        crop_details = None
        if self.video_display.crop_area and crop:
            pixmap_rect = self.video_display.pixmap_item.sceneBoundingRect()
            crop_rect_scene = self.video_display.crop_area

            if pixmap_rect.width() > 0 and pixmap_rect.height() > 0:
                with VideoFileClip(self.video_path) as temp_clip:
                    orig_w, orig_h = temp_clip.size

                scale_x = orig_w / pixmap_rect.width()
                scale_y = orig_h / pixmap_rect.height()

                x1 = (crop_rect_scene.x() - pixmap_rect.x()) * scale_x
                y1 = (crop_rect_scene.y() - pixmap_rect.y()) * scale_y
                w = crop_rect_scene.width() * scale_x
                h = crop_rect_scene.height() * scale_y

                x1_int, y1_int, w_int, h_int = int(x1), int(y1), int(w), int(h)
                if w_int % 2 != 0: w_int -= 1
                if h_int % 2 != 0: h_int -= 1

                x1_int = max(0, x1_int)
                y1_int = max(0, y1_int)
                w_int = min(w_int, orig_w - x1_int)
                h_int = min(h_int, orig_h - y1_int)

                if w_int > 0 and h_int > 0:
                    crop_details = {'x1': x1_int, 'y1': y1_int, 'width': w_int, 'height': h_int}

        self.save_thread = QThread()
        self.save_worker = SaveWorker(self.video_path, save_path, start_time, end_time, crop_details, self.is_muted)
        self.save_worker.moveToThread(self.save_thread)

        self.save_worker.finished.connect(self.on_video_save_complete)
        self.save_thread.started.connect(self.save_worker.run)
        self.save_thread.finished.connect(self.save_worker.deleteLater)
        self.save_thread.finished.connect(self.save_thread.deleteLater)
        self.save_worker.finished.connect(self.save_thread.quit)

        self.save_thread.start()

    def save_audio_only(self):
        if not self.video_path or not self.has_audio:
            return
        save_path, _ = QFileDialog.getSaveFileName(self, "Save Audio", f"{os.path.splitext(self.video_path)[0]}_audio.mp3", "MP3 Files (*.mp3)")
        if not save_path:
            return

        self.update_ui_state(True, is_saving=True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("Saving audio...")
        self.progress_bar.setVisible(True)

        start_time = self.start_frame / self.fps
        end_time = self.end_frame / self.fps

        self.save_audio_thread = QThread()
        self.save_audio_worker = SaveAudioWorker(self.video_path, save_path, start_time, end_time)
        self.save_audio_worker.moveToThread(self.save_audio_thread)

        self.save_audio_worker.finished.connect(self.on_audio_save_complete)
        self.save_audio_thread.started.connect(self.save_audio_worker.run)
        self.save_audio_thread.finished.connect(self.save_audio_worker.deleteLater)
        self.save_audio_thread.finished.connect(self.save_audio_thread.deleteLater)
        self.save_audio_worker.finished.connect(self.save_audio_thread.quit)

        self.save_audio_thread.start()

    def on_video_save_complete(self, error_message, save_path):
        self.progress_bar.setVisible(False)
        if error_message:
            self.show_error_message(f"Error saving file: {error_message}")
            self.update_ui_state(True)
        else:
            print("DEBUG: Video writing complete. Reloading.")
            self.video_path = save_path
            self.load_video()

    def on_audio_save_complete(self, error_message):
        self.progress_bar.setVisible(False)
        self.update_ui_state(True)
        if error_message:
            self.show_error_message(f"Error saving file: {error_message}")
        else:
            print("DEBUG: Audio writing complete.")

    def cleanup_resources(self):
        print("DEBUG: Cleaning up resources...")
        if self.is_playing: self.toggle_play_pause()
        if self.video_capture: self.video_capture.release()
        if self.audio_thread:
            self.audio_thread.stop()
            self.audio_thread.join()
        if self.original_clip: self.original_clip.close()
        self.video_display.reset_crop()
        if hasattr(self, 'waveform_widget'):
            self.waveform_widget.set_waveform_data(None)
        self.video_capture = None
        self.original_clip = None
        self.audio_thread = None
        self.has_audio = False
        print("DEBUG: Resources cleaned.")

    def show_error_message(self, message):
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Critical)
        msg_box.setText(message)
        msg_box.setWindowTitle("Error")
        msg_box.exec_()

    def closeEvent(self, event):
        self.cleanup_resources()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = VideoEditorWindow()
    window.show()
    sys.exit(app.exec_())