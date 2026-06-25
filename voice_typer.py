#!/usr/bin/env python3
import json
import logging
import signal
import sys
import threading
import time
import webbrowser  # Модуль для открытия ссылок в браузере
from pathlib import Path
from typing import List, Optional

import numpy as np
import sounddevice as sd
from pynput import keyboard
import torch
import whisper

import pyautogui
import pyperclip

import tkinter as tk
from tkinter import messagebox

# Модули для работы с системным треем
from PIL import Image, ImageDraw
import pystray

LOCALIZATION = {
    "ru": {
        "loading_whisper": "ЗАГРУЗКА WHISPER...",
        "ready_to_work": "ГОТОВ К РАБОТЕ",
        "recording": "ЗАПИСЬ...",
        "processing": "ОБРАБОТКА...",
        "empty_signal": "ПУСТОЙ СИГНАЛ",
        "ready": "ГОТОВ",
        "not_recognized": "НЕ РАСПОЗНАНО",
        "error": "ОШИБКА",
        "language_status": "ЯЗЫК: {lang}",
        "tray_restore": "Развернуть",
        "tray_exit": "Выход",
        "menu_about": " О программе",
        "menu_settings": " Настройки",
        "menu_exit": " Закрыть программу",
        "about_desc": "Голосовой ввод и распознавание в текст",
        "about_close": "Закрыть",
        "settings_title": "Размер окна программы:",
        "settings_save": "Сохранить",
        "settings_cancel": "Отмена",
        "exit_confirm": "Вы точно желаете закрыть\nпрограмму?",
        "exit_yes": "Да",
        "exit_no": "Нет"
    },
    "en": {
        "loading_whisper": "LOADING WHISPER...",
        "ready_to_work": "READY TO WORK",
        "recording": "RECORDING...",
        "processing": "PROCESSING...",
        "empty_signal": "EMPTY SIGNAL",
        "ready": "READY",
        "not_recognized": "NOT RECOGNIZED",
        "error": "ERROR",
        "language_status": "LANG: {lang}",
        "tray_restore": "Restore",
        "tray_exit": "Exit",
        "menu_about": " About",
        "menu_settings": " Settings",
        "menu_exit": " Exit program",
        "about_desc": "Voice typing and transcription",
        "about_close": "Close",
        "settings_title": "Window size:",
        "settings_save": "Save",
        "settings_cancel": "Cancel",
        "exit_confirm": "Are you sure you want to close\nthe program?",
        "exit_yes": "Yes",
        "exit_no": "No"
    }
}

DEFAULT_CONFIG = {
    "show_window_hotkey": "f8",   # Показать/скрыть окно
    "record_key": "f9",           # Удержание записи (Push-to-Talk)
    "lang_toggle_hotkey": "f10",  # Переключение языка (RU/EN)
    "sample_rate": 16000,
    "channels": 1,
    "model_size": "base",         
    "device": "auto",
    "beam_size": 5,               
    "language": "ru",
    "app_language": "ru",         # Interface language
    "initial_prompt": "Разговор на русском языке. Текст полностью на русском.",
    "silence_guard_seconds": 0.35,
    "typing_backend": "pyautogui",
    "window_size": "260x160"      # Размер окна по умолчанию
}

# Стилизация интерфейса
BG_COLOR = "#2D2B3A"          
PANEL_COLOR = "#3A374C"       
TEXT_COLOR = "#E2DFE9"        
ACCENT_MUTED = "#8E87A4"      
ACCENT_ACTIVE = "#A176FF"     
RECORD_COLOR = "#FF4B4B"      

class VoiceTyperApp:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = self._load_config(config_path)
        self.logger = logging.getLogger("voice-typer")
        
        self._lock = threading.Lock()
        self._recording = False
        self._transcribing = False
        self._stream: Optional[sd.InputStream] = None
        self._frames: List[np.ndarray] = []
        
        self._key_is_pressed = False
        self._window_visible = True
        
        # Localization state
        self._current_status_key = "loading_whisper"
        self._current_status_active = False
        
        # Инициализация Tkinter окна
        self.root = tk.Tk()
        self.root.title("Voice Typer")
        
        # Парсим размер окна из конфига
        self._apply_dimensions_from_config()
        
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", "#010101") 
        
        # Центрирование окна на экране при первом запуске
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{int((sw-self.width)/2)}+{int((sh-self.height)/2)}")
        
        # Холст UI
        self.canvas = tk.Canvas(self.root, bg="#010101", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        self._build_custom_ui()
        
        # Перетаскивание окна мышкой за любое место
        self.canvas.bind("<Button-1>", self._start_move)
        self.canvas.bind("<B1-Motion>", self._do_move)
        
        # Инициализация трея
        self.tray_icon = None
        self._setup_tray()
        
        # Фоновый запуск ИИ Whisper
        self.model = None
        threading.Thread(target=self._load_model, daemon=True).start()
        
        self._listener = None

    def _get_text(self, key: str, **kwargs) -> str:
        # Interface language is now in config["app_language"]
        lang = self.config.get("app_language", "ru")
        if lang not in LOCALIZATION:
            lang = "ru"
        text = LOCALIZATION[lang].get(key, "")
        if kwargs:
            text = text.format(**kwargs)
        return text

    def _load_config(self, path: Path):
        config = dict(DEFAULT_CONFIG)
        if "app_language" not in config:
            config["app_language"] = "ru" # Default app language
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                try: 
                    config.update(json.load(handle))
                except Exception: 
                    pass
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._save_config_to_file(config)
        return config

    def _save_config_to_file(self, config_dict):
        try:
            with self.config_path.open("w", encoding="utf-8") as handle:
                json.dump(config_dict, handle, indent=2)
        except Exception as e:
            self.logger.error(f"Не удалось сохранить конфиг: {e}")

    def _apply_dimensions_from_config(self):
        size_str = self.config.get("window_size", "260x160")
        try:
            w, h = map(int, size_str.split('x'))
        except Exception:
            w, h = 260, 160
        self.width = w
        self.height = h
        self.root.geometry(f"{self.width}x{self.height}")

    def _load_model(self):
        model_size = self.config["model_size"]
        device = "cuda" if torch.cuda.is_available() else "cpu"
        if str(self.config["device"]).strip().lower() != "auto":
            device = self.config["device"]
        self.model = whisper.load_model(model_size, device=device)
        self._update_status_ui("ready_to_work", False)

    def _audio_callback(self, indata, frames, callback_time, status):
        del frames, callback_time
        with self._lock:
            if self._recording:
                self._frames.append(indata[:, 0].copy())

    def _build_custom_ui(self):
        self.canvas.delete("all")
        
        # Динамический радиус скругления и размеры декораций в зависимости от масштаба
        radius = 15 if self.width < 320 else 20
        cap_w = 20 if self.width < 320 else 25
        
        # Основной фон приложения
        self._draw_rounded_rect(0, 0, self.width, self.height, radius, fill=BG_COLOR)
        
        # Верхний декоративный элемент (капсула)
        self._draw_rounded_rect(self.width//2 - cap_w, 10, self.width//2 + cap_w, 15, 2, fill=PANEL_COLOR)
        
        # Кнопка скрытия в трей (Крестик справа вверху)
        self.close_btn = self.canvas.create_text(self.width - 20, 18, text="✕", fill=ACCENT_MUTED, font=("Arial", 11, "bold"))
        self.canvas.tag_bind(self.close_btn, "<Button-1>", lambda e: self.hide_to_tray())
        self.canvas.tag_bind(self.close_btn, "<Enter>", lambda e: self.canvas.itemconfig(self.close_btn, fill="#FF4B4B"))
        self.canvas.tag_bind(self.close_btn, "<Leave>", lambda e: self.canvas.itemconfig(self.close_btn, fill=ACCENT_MUTED))

        # --- ЛЕВАЯ СТОРОНА: Глобус (Переключение языка транскрибации) ---
        self.lang_icon = self.canvas.create_text(45, self.height//2 + 2, text="🌐", fill=ACCENT_MUTED, font=("Segoe UI Symbol", 16))
        self.canvas.tag_bind(self.lang_icon, "<Button-1>", lambda e: self._toggle_language())
        self.canvas.tag_bind(self.lang_icon, "<Enter>", lambda e: self.canvas.itemconfig(self.lang_icon, fill=ACCENT_ACTIVE))
        self.canvas.tag_bind(self.lang_icon, "<Leave>", lambda e: self.canvas.itemconfig(self.lang_icon, fill=ACCENT_MUTED))

        # Текст текущего языка ПОД глобусом
        self.lang_label = self.canvas.create_text(45, self.height//2 + 20, text=self.config["language"].upper(), fill=ACCENT_MUTED, font=("Arial", 8, "bold"))

        # --- ПРАВАЯ СТОРОНА: Шестеренка (Настройки и Выход) ---
        self.gear_icon = self.canvas.create_text(self.width - 45, self.height//2 + 2, text="⚙", fill=ACCENT_MUTED, font=("Segoe UI Symbol", 18))
        self.canvas.tag_bind(self.gear_icon, "<Button-1>", self._show_gear_menu)
        self.canvas.tag_bind(self.gear_icon, "<Enter>", lambda e: self.canvas.itemconfig(self.gear_icon, fill=ACCENT_ACTIVE))
        self.canvas.tag_bind(self.gear_icon, "<Leave>", lambda e: self.canvas.itemconfig(self.gear_icon, fill=ACCENT_MUTED))

        # Центральный блок: Большое кольцо
        self.cx, self.cy = self.width // 2, self.height // 2 + 5
        self.r_outer = 32 if self.width < 320 else 38
        self.btn_outer = self.canvas.create_oval(self.cx-self.r_outer, self.cy-self.r_outer, self.cx+self.r_outer, self.cy+self.r_outer, outline=ACCENT_MUTED, width=2)
        
        # Отрисовка интерактивной иконки микрофона внутри круга
        self.mic_elements = []
        self._draw_mic_icon(ACCENT_MUTED)

        # Нижняя строка состояния
        status_val = ""
        if getattr(self, "_current_status_key", None):
            status_val = self._get_text(self._current_status_key)
        else:
            status_val = self._get_text("loading_whisper") if not self.model else self._get_text("ready_to_work")
            
        self.status_text = self.canvas.create_text(
            self.cx, self.height - 18, 
            text=status_val.upper(), 
            fill=RECORD_COLOR if getattr(self, "_current_status_active", False) else ACCENT_MUTED, 
            font=("Arial", 7 if self.width < 320 else 8, "bold")
        )

    def _draw_mic_icon(self, color):
        """Отрисовка кастомного значка микрофона, масштабированного под текущее кольцо"""
        for el in self.mic_elements:
            self.canvas.delete(el)
        self.mic_elements.clear()
        
        scale = 1 if self.width < 320 else 1.2
        w_capsule = int(5 * scale)
        h_capsule_top = int(11 * scale)
        h_capsule_bot = int(3 * scale)
        r_arc = int(10 * scale)
        h_leg = int(14 * scale)
        
        m_capsule = self._draw_rounded_rect(self.cx-w_capsule, self.cy-h_capsule_top, self.cx+w_capsule, self.cy+h_capsule_bot, 5, fill=color)
        m_arc = self.canvas.create_arc(self.cx-r_arc, self.cy-5, self.cx+r_arc, self.cy+r_arc-1, start=180, extent=180, style=tk.ARC, outline=color, width=2)
        m_leg = self.canvas.create_line(self.cx, self.cy+r_arc-1, self.cx, self.cy+h_leg, fill=color, width=2)
        
        self.mic_elements.extend([m_capsule, m_arc, m_leg])

    def _draw_rounded_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [x1+r, y1, x1+r, y1, x2-r, y1, x2-r, y1, x2, y1, x2, y1+r, x2, y1+r, x2, y2-r, x2, y2-r, x2, y2, x2-r, y2, x2-r, y2, x1+r, y2, x1+r, y2, x1, y2, x1, y2-r, x1, y2-r, x1, y1+r, x1, y1+r, x1, y1]
        return self.canvas.create_polygon(points, **kwargs, smooth=True)

    def _show_gear_menu(self, event):
        """Контекстное меню шестерёнки с добавленным пунктом 'О программе'"""
        menu = tk.Menu(self.root, tearoff=0, bg=PANEL_COLOR, fg=TEXT_COLOR, activebackground=ACCENT_ACTIVE, activeforeground="#FFFFFF", bd=0)
        menu.add_command(label=self._get_text("menu_about"), command=self.open_about_window)
        menu.add_command(label=self._get_text("menu_settings"), command=self.open_settings_window)
        menu.add_separator()
        menu.add_command(label=self._get_text("menu_exit"), command=self.ask_exit)
        menu.post(event.x_root, event.y_root)

    def open_about_window(self):
        """Модальное окно 'О программе' с кликабельной ссылкой GitHub"""
        about_win = tk.Toplevel(self.root)
        about_win.geometry("300x150")
        about_win.overrideredirect(True)
        about_win.attributes("-topmost", True)
        about_win.configure(bg=PANEL_COLOR)
        
        # Центрируем поверх главного интерфейса
        mx = self.root.winfo_x() + (self.width - 300) // 2
        my = self.root.winfo_y() + (self.height - 150) // 2
        about_win.geometry(f"+{mx}+{my}")
        
        # Заголовок
        tk.Label(about_win, text="Voice Typer", bg=PANEL_COLOR, fg=ACCENT_ACTIVE, font=("Arial", 12, "bold")).pack(pady=(15, 2))
        
        # Описание
        tk.Label(about_win, text=self._get_text("about_desc"), bg=PANEL_COLOR, fg=TEXT_COLOR, font=("Arial", 9)).pack(pady=2)
        
        # Ссылка на GitHub
        url = "https://github.com/mshapps-ru/voice_typer"
        link_label = tk.Label(about_win, text=url, bg=PANEL_COLOR, fg="#64B5F6", font=("Arial", 9, "underline"), cursor="hand2")
        link_label.pack(pady=10)
        
        # Обработка клика по ссылке
        link_label.bind("<Button-1>", lambda e: webbrowser.open(url))
        # Свечение ссылки при наведении курсора
        link_label.bind("<Enter>", lambda e: link_label.config(fg=ACCENT_ACTIVE))
        link_label.bind("<Leave>", lambda e: link_label.config(fg="#64B5F6"))
        
        # Кнопка закрытия окна
        tk.Button(about_win, text=self._get_text("about_close"), command=about_win.destroy, bg=BG_COLOR, fg=TEXT_COLOR, font=("Arial", 9, "bold"), bd=0, width=10, pady=2).pack(pady=5)

    def open_settings_window(self):
        """Модальное окно для изменения настроек"""
        settings_win = tk.Toplevel(self.root)
        settings_win.geometry("260x220")
        settings_win.overrideredirect(True)
        settings_win.attributes("-topmost", True)
        settings_win.configure(bg=PANEL_COLOR)
        
        # Центрируем поверх главного интерфейса
        mx = self.root.winfo_x() + (self.width - 260) // 2
        my = self.root.winfo_y() + (self.height - 220) // 2
        settings_win.geometry(f"+{mx}+{my}")
        
        # Размер окна
        tk.Label(settings_win, text=self._get_text("settings_title"), bg=PANEL_COLOR, fg=TEXT_COLOR, font=("Arial", 10, "bold")).pack(pady=(10, 5))
        
        sizes = ["260x160", "320x200", "480x240"]
        current_size = self.config.get("window_size", "260x160")
        selected_size = tk.StringVar(settings_win)
        selected_size.set(current_size)
        opt_size = tk.OptionMenu(settings_win, selected_size, *sizes)
        opt_size.config(bg=BG_COLOR, fg=TEXT_COLOR, activebackground=ACCENT_ACTIVE, bd=0)
        opt_size["menu"].config(bg=PANEL_COLOR, fg=TEXT_COLOR)
        opt_size.pack(pady=5)

        # Язык интерфейса
        tk.Label(settings_win, text="Язык приложения:", bg=PANEL_COLOR, fg=TEXT_COLOR, font=("Arial", 10, "bold")).pack(pady=(10, 5))
        app_langs = ["ru", "en"]
        current_app_lang = self.config.get("app_language", "ru")
        selected_app_lang = tk.StringVar(settings_win)
        selected_app_lang.set(current_app_lang)
        opt_app_lang = tk.OptionMenu(settings_win, selected_app_lang, *app_langs)
        opt_app_lang.config(bg=BG_COLOR, fg=TEXT_COLOR, activebackground=ACCENT_ACTIVE, bd=0)
        opt_app_lang["menu"].config(bg=PANEL_COLOR, fg=TEXT_COLOR)
        opt_app_lang.pack(pady=5)
        
        def save_settings():
            self.config["window_size"] = selected_size.get()
            self.config["app_language"] = selected_app_lang.get()
            self._save_config_to_file(self.config)
            
            self._apply_dimensions_from_config()
            self._build_custom_ui()
            
            # Update tray menu language
            self._update_tray_menu()
            
            settings_win.destroy()
            
        btn_frame = tk.Frame(settings_win, bg=PANEL_COLOR)
        btn_frame.pack(pady=15)
        
        tk.Button(btn_frame, text=self._get_text("settings_save"), command=save_settings, bg=ACCENT_ACTIVE, fg="#FFFFFF", font=("Arial", 9, "bold"), bd=0, width=10, padx=5).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text=self._get_text("settings_cancel"), command=settings_win.destroy, bg=BG_COLOR, fg=TEXT_COLOR, font=("Arial", 9, "bold"), bd=0, width=10, padx=5).pack(side=tk.LEFT, padx=5)

    def ask_exit(self):
        ask_win = tk.Toplevel(self.root)
        ask_win.geometry("240x110")
        ask_win.overrideredirect(True)
        ask_win.attributes("-topmost", True)
        ask_win.configure(bg=PANEL_COLOR)
        
        mx = self.root.winfo_x() + (self.width - 240) // 2
        my = self.root.winfo_y() + (self.height - 110) // 2
        ask_win.geometry(f"+{mx}+{my}")
        
        tk.Label(ask_win, text=self._get_text("exit_confirm"), bg=PANEL_COLOR, fg=TEXT_COLOR, font=("Arial", 9, "bold")).pack(pady=15)
        
        btn_frame = tk.Frame(ask_win, bg=PANEL_COLOR)
        btn_frame.pack()
        
        def confirm_yes():
            ask_win.destroy()
            self.hard_exit()
            
        def confirm_no():
            ask_win.destroy()

        tk.Button(btn_frame, text=self._get_text("exit_yes"), command=confirm_yes, bg=RECORD_COLOR, fg="#FFFFFF", font=("Arial", 9, "bold"), bd=0, width=8, padx=5).pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text=self._get_text("exit_no"), command=confirm_no, bg=BG_COLOR, fg=TEXT_COLOR, font=("Arial", 9, "bold"), bd=0, width=8, padx=5).pack(side=tk.LEFT, padx=10)

    def _toggle_language(self):
        """Переключение языка транскрибации (Globe)"""
        current = self.config["language"]
        self.config["language"] = "en" if current == "ru" else "ru"
        self._save_config_to_file(self.config)
        
        self.root.after(0, lambda: self.canvas.itemconfig(self.lang_label, text=self.config["language"].upper()))
        
        # Обновляем статус, используя интерфейсный язык для форматирования
        lang_text = self._get_text("language_status", lang=self.config["language"].upper())
        self._update_status_ui(lang_text, False, is_key=False)
        self._update_tray_menu()

    def _update_status_ui(self, key_or_text: str, active: bool = False, is_key: bool = True):
        if is_key:
            self._current_status_key = key_or_text
            self._current_status_active = active
            text = self._get_text(key_or_text)
        else:
            self._current_status_key = None
            self._current_status_active = active
            text = key_or_text

        def update():
            self.canvas.itemconfig(self.status_text, text=text.upper())
            if active: 
                self.canvas.itemconfig(self.btn_outer, outline=RECORD_COLOR, width=3)
                self.canvas.itemconfig(self.status_text, fill=RECORD_COLOR)
                self._draw_mic_icon(RECORD_COLOR)
            else: 
                color = ACCENT_ACTIVE if self.model else ACCENT_MUTED
                self.canvas.itemconfig(self.btn_outer, outline=color, width=2)
                self.canvas.itemconfig(self.status_text, fill=ACCENT_MUTED)
                self._draw_mic_icon(color)
        self.root.after(0, update)

    def _start_move(self, event):
        self.x = event.x
        self.y = event.y

    def _do_move(self, event):
        x = self.root.winfo_x() + (event.x - self.x)
        y = self.root.winfo_y() + (event.y - self.y)
        self.root.geometry(f"+{x}+{y}")

    def _setup_tray(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        color = (161, 118, 255, 255)  
        
        draw.rounded_rectangle([22, 12, 42, 38], radius=10, fill=color)
        draw.arc([14, 20, 50, 48], start=0, end=180, fill=color, width=4)
        draw.line([32, 46, 32, 56], fill=color, width=4)
        draw.line([20, 56, 44, 56], fill=color, width=4)
        
        menu = pystray.Menu(
            pystray.MenuItem(self._get_text("tray_restore"), self.show_from_tray, default=True),
            pystray.MenuItem(self._get_text("tray_exit"), self.ask_exit)
        )
        self.tray_icon = pystray.Icon("VoiceTyper", img, "Voice Typer (Whisper)", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _update_tray_menu(self):
        if self.tray_icon:
            self.tray_icon.menu = pystray.Menu(
                pystray.MenuItem(self._get_text("tray_restore"), self.show_from_tray, default=True),
                pystray.MenuItem(self._get_text("tray_exit"), self.ask_exit)
            )

    def hide_to_tray(self):
        self.root.after(0, self._unsafe_hide_to_tray)

    def _unsafe_hide_to_tray(self):
        self.root.withdraw()
        self._window_visible = False

    def show_from_tray(self):
        self.root.after(0, self._unsafe_show_from_tray)

    def _unsafe_show_from_tray(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self._window_visible = True

    def _on_press(self, key):
        try: key_name = key.name if hasattr(key, 'name') else key.char
        except AttributeError: return
        
        hk_show = self.config["show_window_hotkey"]
        hk_record = self.config["record_key"]
        hk_lang = self.config.get("lang_toggle_hotkey", "f10")

        if key_name == hk_show:
            if self._window_visible: self.hide_to_tray()
            else: self.show_from_tray()
            return
            
        if key_name == hk_lang:
            self._toggle_language()
            return
            
        if key_name == hk_record:
            if not self._key_is_pressed:
                self._key_is_pressed = True
                self.start_recording()

    def _on_release(self, key):
        try: key_name = key.name if hasattr(key, 'name') else key.char
        except AttributeError: return
        hk_record = self.config["record_key"]

        if key_name == hk_record:
            if self._key_is_pressed:
                self._key_is_pressed = False
                self.shutdown_stream_and_type()

    def start_recording(self):
        if not self.model: return
        with self._lock:
            if self._transcribing or self._recording: return
            self._frames = []
            try:
                sr, ch = int(self.config["sample_rate"]), int(self.config["channels"])
                self._stream = sd.InputStream(samplerate=sr, channels=ch, dtype="float32", callback=self._audio_callback)
                self._stream.start()
                self._recording = True
            except Exception: return
        self._update_status_ui("recording", True)

    def shutdown_stream_and_type(self):
        with self._lock:
            if not self._recording: return
            self._recording = False
            stream, self._stream = self._stream, None
            frames, self._frames = list(self._frames), []
            self._transcribing = True
            
        if stream:
            stream.stop()
            stream.close()
            
        self._update_status_ui("processing", False)
        threading.Thread(target=self._transcribe, args=(frames, self.config["language"]), daemon=True).start()

    def _transcribe(self, frames: List[np.ndarray], language: str):
        try:
            if not frames: 
                self._update_status_ui("empty_signal", False)
                return
            audio = np.concatenate(frames).astype(np.float32)
            
            prompt = "Разговор на русском языке. Текст полностью на русском." if language == "ru" else "English conversation."
            result = self.model.transcribe(
                audio=audio, language=language, task="transcribe", initial_prompt=prompt,
                beam_size=int(self.config["beam_size"]), fp16=self.model.device.type == "cuda", temperature=0.0
            )
            text = str(result.get("text", "")).strip()
            if text:
                self._type_text(text)
                self._update_status_ui("ready", False)
            else:
                self._update_status_ui("not_recognized", False)
        except Exception:
            self._update_status_ui("error", False)
        finally:
            with self._lock: self._transcribing = False

    def _type_text(self, text: str):
        try: old = pyperclip.paste()
        except Exception: old = ""
        pyperclip.copy(text)
        time.sleep(0.05)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.1)
        try: pyperclip.copy(old)
        except Exception: pass

    def run(self):
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()
        self.root.mainloop()

    def hard_exit(self):
        if self._listener: self._listener.stop()
        if self.tray_icon: self.tray_icon.stop()
        try: self.root.destroy()
        except Exception: pass
        sys.exit(0)


def main():
    logging.basicConfig(level=logging.INFO)
    app = VoiceTyperApp(Path.home() / ".config" / "voice-typer" / "config.json")
    signal.signal(signal.SIGINT, lambda s, f: app.hard_exit())
    signal.signal(signal.SIGTERM, lambda s, f: app.hard_exit())
    try: app.run()
    except KeyboardInterrupt: app.hard_exit()
    return 0

if __name__ == "__main__":
    sys.exit(main())
