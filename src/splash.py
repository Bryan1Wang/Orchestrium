"""Tiny loading splash shown while the heavy imports (onnxruntime etc.) load.

Uses tkinter rather than Qt because Qt must not be imported before `main`
(see main_gui/orchestrium) -- tkinter doesn't touch Qt's DLL search path, so it can
safely appear first and be torn down before PySide6 loads. This module must
stay free of Qt imports.
"""


def show():
    import tkinter as tk

    splash = tk.Tk()
    splash.overrideredirect(True)
    width, height = 220, 70
    screen_w = splash.winfo_screenwidth()
    screen_h = splash.winfo_screenheight()
    x, y = (screen_w - width) // 2, (screen_h - height) // 2
    splash.geometry(f"{width}x{height}+{x}+{y}")
    tk.Label(splash, text="Loading…", font=("Segoe UI", 12)).pack(expand=True, fill="both")
    splash.update()
    return splash
