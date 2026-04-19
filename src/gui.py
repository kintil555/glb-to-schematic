"""
GLB to Schematic - GUI Wrapper
Drag & drop interface for the converter
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Make sure src/ is importable when running as EXE
sys.path.insert(0, os.path.dirname(__file__))
from converter import convert, BLOCK_MAP


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GLB to Schematic Converter")
        self.resizable(False, False)
        self.configure(bg="#1e1e2e")

        self._build_ui()
        self._center()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        PAD = 16
        BG  = "#1e1e2e"
        FG  = "#cdd6f4"
        ACC = "#89b4fa"
        BTN = "#313244"
        ENT = "#313244"

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TLabel",      background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("Accent.TLabel", background=BG, foreground=ACC, font=("Segoe UI", 11, "bold"))
        style.configure("TButton",     background=BTN, foreground=FG, font=("Segoe UI", 10), relief="flat", padding=6)
        style.map("TButton",           background=[("active", "#45475a")])
        style.configure("TEntry",      fieldbackground=ENT, foreground=FG, insertcolor=FG)
        style.configure("TCombobox",   fieldbackground=ENT, foreground=FG, background=BTN)
        style.configure("TScale",      background=BG, troughcolor=BTN)
        style.configure("TProgressbar", troughcolor=BTN, background=ACC)

        root = tk.Frame(self, bg=BG, padx=PAD, pady=PAD)
        root.pack(fill="both", expand=True)

        # Title
        ttk.Label(root, text="GLB -> Schematic Converter", style="Accent.TLabel").grid(
            row=0, column=0, columnspan=3, pady=(0, PAD), sticky="w")

        # Input file
        ttk.Label(root, text="Input GLB/GLTF:").grid(row=1, column=0, sticky="w", pady=4)
        self.input_var = tk.StringVar()
        ttk.Entry(root, textvariable=self.input_var, width=45).grid(row=1, column=1, padx=8, pady=4)
        ttk.Button(root, text="Browse", command=self._browse_input).grid(row=1, column=2, pady=4)

        # Output file
        ttk.Label(root, text="Output .schematic:").grid(row=2, column=0, sticky="w", pady=4)
        self.output_var = tk.StringVar()
        ttk.Entry(root, textvariable=self.output_var, width=45).grid(row=2, column=1, padx=8, pady=4)
        ttk.Button(root, text="Browse", command=self._browse_output).grid(row=2, column=2, pady=4)

        # Resolution
        ttk.Label(root, text="Resolution:").grid(row=3, column=0, sticky="w", pady=4)
        res_frame = tk.Frame(root, bg=BG)
        res_frame.grid(row=3, column=1, sticky="w", padx=8)
        self.res_var = tk.IntVar(value=64)
        self.res_label = ttk.Label(res_frame, text="64")
        tk.Scale(res_frame, from_=8, to=256, orient="horizontal", variable=self.res_var,
                 bg=BG, fg=FG, troughcolor=BTN, highlightthickness=0, length=200,
                 command=lambda v: self.res_label.config(text=str(int(float(v))))
                 ).pack(side="left")
        self.res_label.pack(side="left", padx=6)

        # Block type
        ttk.Label(root, text="Block type:").grid(row=4, column=0, sticky="w", pady=4)
        self.block_var = tk.StringVar(value="stone")
        blocks = sorted(BLOCK_MAP.keys())
        cb = ttk.Combobox(root, textvariable=self.block_var, values=blocks, state="readonly", width=20)
        cb.grid(row=4, column=1, sticky="w", padx=8, pady=4)

        # Fill interior
        ttk.Label(root, text="Fill interior:").grid(row=5, column=0, sticky="w", pady=4)
        self.fill_var = tk.BooleanVar(value=True)
        chk = tk.Checkbutton(root, variable=self.fill_var, bg=BG, fg=FG,
                              activebackground=BG, activeforeground=ACC,
                              selectcolor=BTN, relief="flat")
        chk.grid(row=5, column=1, sticky="w", padx=8)

        # Multi-block
        ttk.Label(root, text="Multi-block:").grid(row=7, column=0, sticky="w", pady=4)
        self.multi_var = tk.BooleanVar(value=True)
        tk.Checkbutton(root, variable=self.multi_var, bg=BG, fg=FG,
                       activebackground=BG, activeforeground=ACC,
                       selectcolor=BTN, relief="flat",
                       text="Auto-detect blocks from UV texture").grid(
            row=6, column=1, sticky="w", padx=8, columnspan=2)

        # Scale
        ttk.Label(root, text="Scale:").grid(row=7, column=0, sticky="w", pady=4)
        self.scale_var = tk.DoubleVar(value=1.0)
        scale_frame = tk.Frame(root, bg=BG)
        scale_frame.grid(row=7, column=1, sticky="w", padx=8)
        self.scale_label = ttk.Label(scale_frame, text="1.0")
        tk.Scale(scale_frame, from_=0.1, to=5.0, resolution=0.1, orient="horizontal",
                 variable=self.scale_var, bg=BG, fg=FG, troughcolor=BTN,
                 highlightthickness=0, length=200,
                 command=lambda v: self.scale_label.config(text=f"{float(v):.1f}")
                 ).pack(side="left")
        self.scale_label.pack(side="left", padx=6)

        # Separator
        tk.Frame(root, bg="#45475a", height=1).grid(row=8, column=0, columnspan=3,
                                                     sticky="ew", pady=PAD)

        # Progress bar
        self.progress = ttk.Progressbar(root, mode="indeterminate", length=400)
        self.progress.grid(row=9, column=0, columnspan=3, pady=(0, 8))

        # Log box
        self.log = tk.Text(root, height=8, bg="#11111b", fg=FG, font=("Consolas", 9),
                           relief="flat", state="disabled", wrap="word")
        self.log.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(0, PAD))

        # Convert button
        self.btn = ttk.Button(root, text="Convert", command=self._start)
        self.btn.grid(row=11, column=0, columnspan=3, ipadx=20, ipady=4)

    def _center(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"+{x}+{y}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _browse_input(self):
        path = filedialog.askopenfilename(
            title="Select GLB/GLTF file",
            filetypes=[("3D Models", "*.glb *.gltf"), ("All files", "*.*")]
        )
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                out = os.path.splitext(path)[0] + ".schematic"
                self.output_var.set(out)

    def _browse_output(self):
        path = filedialog.asksaveasfilename(
            title="Save schematic as",
            defaultextension=".schematic",
            filetypes=[("Schematic", "*.schematic"), ("All files", "*.*")]
        )
        if path:
            self.output_var.set(path)

    def _log(self, msg):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    # ── Conversion ────────────────────────────────────────────────────────────

    def _start(self):
        inp = self.input_var.get().strip()
        out = self.output_var.get().strip()

        if not inp:
            messagebox.showwarning("Missing input", "Please select a GLB/GLTF file.")
            return
        if not out:
            messagebox.showwarning("Missing output", "Please specify an output path.")
            return
        if not os.path.isfile(inp):
            messagebox.showerror("File not found", f"Input file not found:\n{inp}")
            return

        self.btn.config(state="disabled")
        self.progress.start(10)
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        self._log(f"Starting conversion...")
        self._log(f"  Input      : {inp}")
        self._log(f"  Output     : {out}")
        self._log(f"  Resolution : {self.res_var.get()}")
        self._log(f"  Block      : {self.block_var.get()}")
        self._log(f"  Fill       : {self.fill_var.get()}")
        self._log(f"  Scale      : {self.scale_var.get():.1f}")

        # Redirect stdout to log box
        class LogRedirect:
            def __init__(self, callback): self.cb = callback
            def write(self, s):
                if s.strip(): self.cb(s.rstrip())
            def flush(self): pass

        old_stdout = sys.stdout
        sys.stdout = LogRedirect(self._log)

        def run():
            try:
                convert(
                    inp, out,
                    resolution=self.res_var.get(),
                    block=self.block_var.get(),
                    fill=self.fill_var.get(),
                    multi_block=self.multi_var.get(),
                    scale=self.scale_var.get(),
                )
                sys.stdout = old_stdout
                self.after(0, self._done_ok, out)
            except Exception as e:
                sys.stdout = old_stdout
                self.after(0, self._done_err, str(e))

        threading.Thread(target=run, daemon=True).start()

    def _done_ok(self, out):
        self.progress.stop()
        self.btn.config(state="normal")
        self._log(f"\n[OK] Done! Saved to: {out}")
        messagebox.showinfo("Success", f"Conversion complete!\n\nSaved to:\n{out}")

    def _done_err(self, err):
        self.progress.stop()
        self.btn.config(state="normal")
        self._log(f"\n[ERROR] {err}")
        messagebox.showerror("Conversion failed", f"Error:\n{err}")


if __name__ == "__main__":
    app = App()
    app.mainloop()
