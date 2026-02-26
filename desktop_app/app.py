import json
import queue
import threading
from pathlib import Path
from typing import Optional
from tkinter import END, BOTH, DISABLED, NORMAL, StringVar, Tk, filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:8000/contract"
POLL_INTERVAL_MS = 1200


class ContractReviewDesktopApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("Contract Review Desktop")
        self.root.geometry("980x760")
        self.root.minsize(840, 620)

        self.base_url_var = StringVar(value=DEFAULT_BASE_URL)
        self.file_var = StringVar(value="No file selected")
        self.status_var = StringVar(value="Idle")
        self.stage_var = StringVar(value="-")
        self.job_id_var = StringVar(value="-")
        self.progress_var = StringVar(value="0%")

        self.session = requests.Session()
        self.selected_pdf: Optional[Path] = None
        self.current_job_id: Optional[int] = None
        self.polling = False
        self.done_result: Optional[dict] = None
        self.ui_queue: queue.Queue = queue.Queue()

        self._build_ui()
        self._process_ui_queue()
        self._set_running(False)

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill=BOTH, expand=True)

        cfg = ttk.LabelFrame(frame, text="Backend")
        cfg.pack(fill="x")

        ttk.Label(cfg, text="Base URL").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        self.base_url_entry = ttk.Entry(cfg, textvariable=self.base_url_var)
        self.base_url_entry.grid(row=0, column=1, padx=8, pady=8, sticky="ew")
        self.connect_btn = ttk.Button(cfg, text="Connect", command=self.on_connect_clicked)
        self.connect_btn.grid(row=0, column=2, padx=8, pady=8, sticky="e")
        cfg.columnconfigure(1, weight=1)

        file_box = ttk.LabelFrame(frame, text="Input PDF")
        file_box.pack(fill="x", pady=(12, 0))

        ttk.Label(file_box, textvariable=self.file_var).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        self.pick_btn = ttk.Button(file_box, text="Pick PDF", command=self.on_pick_clicked)
        self.pick_btn.grid(row=0, column=1, padx=8, pady=8, sticky="e")
        file_box.columnconfigure(0, weight=1)

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(12, 0))
        self.start_btn = ttk.Button(actions, text="Start Analyze", command=self.on_start_clicked)
        self.start_btn.pack(side="left")
        self.export_btn = ttk.Button(actions, text="Export PDF", command=self.on_export_clicked)
        self.export_btn.pack(side="left", padx=(8, 0))
        self.clear_btn = ttk.Button(actions, text="Clear Result", command=self.on_clear_clicked)
        self.clear_btn.pack(side="left", padx=(8, 0))

        state = ttk.LabelFrame(frame, text="Job Status")
        state.pack(fill="x", pady=(12, 0))
        ttk.Label(state, text="Job ID").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        ttk.Label(state, textvariable=self.job_id_var).grid(row=0, column=1, padx=8, pady=6, sticky="w")
        ttk.Label(state, text="Status").grid(row=0, column=2, padx=8, pady=6, sticky="w")
        ttk.Label(state, textvariable=self.status_var).grid(row=0, column=3, padx=8, pady=6, sticky="w")
        ttk.Label(state, text="Stage").grid(row=1, column=0, padx=8, pady=6, sticky="w")
        ttk.Label(state, textvariable=self.stage_var).grid(row=1, column=1, padx=8, pady=6, sticky="w")
        ttk.Label(state, text="Progress").grid(row=1, column=2, padx=8, pady=6, sticky="w")
        ttk.Label(state, textvariable=self.progress_var).grid(row=1, column=3, padx=8, pady=6, sticky="w")
        self.progressbar = ttk.Progressbar(state, orient="horizontal", mode="determinate", maximum=100)
        self.progressbar.grid(row=2, column=0, columnspan=4, padx=8, pady=(6, 10), sticky="ew")
        state.columnconfigure(3, weight=1)

        result = ttk.LabelFrame(frame, text="Result")
        result.pack(fill=BOTH, expand=True, pady=(12, 0))
        self.result_text = ScrolledText(result, wrap="word")
        self.result_text.pack(fill=BOTH, expand=True, padx=8, pady=8)

    def _process_ui_queue(self):
        while True:
            try:
                kind, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "error":
                self._set_running(False)
                messagebox.showerror("Error", str(payload))
            elif kind == "connected":
                messagebox.showinfo("Connected", "Connection OK, CSRF cookie ready.")
            elif kind == "started":
                job_id = int(payload)
                self.current_job_id = job_id
                self.done_result = None
                self.job_id_var.set(str(job_id))
                self.status_var.set("running")
                self.stage_var.set("submitted")
                self.progressbar["value"] = 1
                self.progress_var.set("1%")
                self.result_text.delete("1.0", END)
                self.result_text.insert(END, f"Job {job_id} started.\n")
                self.polling = True
                self._poll_loop()
            elif kind == "poll":
                self._apply_poll_data(payload)  # type: ignore[arg-type]
            elif kind == "exported":
                messagebox.showinfo("Exported", f"Saved to:\n{payload}")

        self.root.after(120, self._process_ui_queue)

    def _set_running(self, running: bool):
        if running:
            self.start_btn.config(state=DISABLED)
            self.connect_btn.config(state=DISABLED)
            self.pick_btn.config(state=DISABLED)
            self.base_url_entry.config(state=DISABLED)
        else:
            self.start_btn.config(state=NORMAL)
            self.connect_btn.config(state=NORMAL)
            self.pick_btn.config(state=NORMAL)
            self.base_url_entry.config(state=NORMAL)

    def _normalized_base(self) -> str:
        return self.base_url_var.get().strip().rstrip("/")

    def _api(self, path: str) -> str:
        return self._normalized_base() + path

    def _ensure_csrf(self):
        resp = self.session.get(self._normalized_base() + "/", timeout=15)
        resp.raise_for_status()
        if "csrftoken" not in self.session.cookies:
            raise RuntimeError("No csrftoken cookie. Check Django endpoint.")

    def on_connect_clicked(self):
        def worker():
            try:
                self._ensure_csrf()
                self.ui_queue.put(("connected", None))
            except Exception as exc:
                self.ui_queue.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def on_pick_clicked(self):
        file_path = filedialog.askopenfilename(
            title="Select PDF",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")],
        )
        if not file_path:
            return
        picked = Path(file_path)
        if picked.suffix.lower() != ".pdf":
            messagebox.showerror("Invalid file", "Please select a PDF file.")
            return
        self.selected_pdf = picked
        self.file_var.set(str(picked))

    def on_start_clicked(self):
        if self.selected_pdf is None:
            messagebox.showwarning("No file", "Please pick a PDF file first.")
            return

        self._set_running(True)
        self.status_var.set("submitting")
        self.stage_var.set("uploading")
        self.progressbar["value"] = 0
        self.progress_var.set("0%")

        def worker():
            try:
                self._ensure_csrf()
                csrf = self.session.cookies.get("csrftoken", "")
                with self.selected_pdf.open("rb") as fp:
                    files = {"file": (self.selected_pdf.name, fp, "application/pdf")}
                    resp = self.session.post(
                        self._api("/api/start/"),
                        files=files,
                        headers={"X-CSRFToken": csrf},
                        timeout=120,
                    )
                if resp.status_code != 200:
                    raise RuntimeError(f"Start failed: {resp.status_code} {resp.text[:500]}")
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Start failed: {data}")
                self.ui_queue.put(("started", int(data["job_id"])))
            except Exception as exc:
                self.ui_queue.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_loop(self):
        if not self.polling or self.current_job_id is None:
            self._set_running(False)
            return

        job_id = self.current_job_id

        def worker():
            try:
                resp = self.session.get(self._api(f"/api/status/{job_id}/"), timeout=20)
                if resp.status_code != 200:
                    raise RuntimeError(f"Status failed: {resp.status_code} {resp.text[:300]}")
                data = resp.json()
                self.ui_queue.put(("poll", data))
            except Exception as exc:
                self.ui_queue.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_poll_data(self, data: dict):
        status = str(data.get("status", "running"))
        stage = str(data.get("stage", "-"))
        progress = int(data.get("progress", 0) or 0)

        self.status_var.set(status)
        self.stage_var.set(stage)
        self.progressbar["value"] = max(0, min(100, progress))
        self.progress_var.set(f"{max(0, min(100, progress))}%")

        if status == "done":
            self.polling = False
            self._set_running(False)
            self.done_result = data
            self._render_done(data)
            return

        if status == "error":
            self.polling = False
            self._set_running(False)
            err = data.get("error") or "Unknown error"
            messagebox.showerror("Job failed", str(err))
            return

        self.root.after(POLL_INTERVAL_MS, self._poll_loop)

    def _render_done(self, data: dict):
        result_json = data.get("result_json")
        result_markdown = data.get("result_markdown") or ""
        self.result_text.delete("1.0", END)

        if isinstance(result_json, dict):
            self.result_text.insert(END, json.dumps(result_json, ensure_ascii=False, indent=2))
        else:
            self.result_text.insert(END, result_markdown)

    def on_export_clicked(self):
        if self.current_job_id is None:
            messagebox.showwarning("No job", "Run analysis first.")
            return

        save_path = filedialog.asksaveasfilename(
            title="Save PDF",
            defaultextension=".pdf",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")],
            initialfile=f"contract_review_job_{self.current_job_id}.pdf",
        )
        if not save_path:
            return

        target = Path(save_path)

        def worker():
            try:
                resp = self.session.get(self._api(f"/api/export_pdf/{self.current_job_id}/"), timeout=120)
                if resp.status_code != 200:
                    raise RuntimeError(f"Export failed: {resp.status_code} {resp.text[:500]}")
                target.write_bytes(resp.content)
                self.ui_queue.put(("exported", str(target)))
            except Exception as exc:
                self.ui_queue.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def on_clear_clicked(self):
        self.done_result = None
        self.result_text.delete("1.0", END)
        self.status_var.set("Idle")
        self.stage_var.set("-")
        self.progressbar["value"] = 0
        self.progress_var.set("0%")
        self.job_id_var.set("-")


def main():
    root = Tk()
    app = ContractReviewDesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
