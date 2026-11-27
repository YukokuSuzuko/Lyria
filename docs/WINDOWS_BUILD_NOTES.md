# Windows build correction

The original 0.11.0.dev2 source ZIP had SHA-256
`7eda2cd55ad58ccc972225370660cf704e0921c3e115fa1751447632cc0d983f`.

Python 3.12 / Tk 8.6 Windows testing exposed a teardown error in the desktop
test fixtures and the offline workflow diagnostic: calling `root.after_cancel`
on a timer registered by a child widget unregisters the child's Python Tcl
command. Destroying the child then tries to delete that command again.

The shared `tk_lifecycle.destroy_root` cleanup cancels Tcl timers before final
root destruction and leaves command disposal with each owning widget. The four
desktop test fixtures and the workflow diagnostic now use it. A real Tk
regression test covers a pending child-owned timer and a root-owned timer.

This correction does not change the resize/seek algorithms, migrate the GUI
framework, or establish a cause for the earlier user-reported video-import exit.
The portable self-check and build-test evidence describe only checks actually
performed; real-song editing, IME, native drag/drop, and visual smoothness still
need user acceptance.

A second Windows-only failure was reproduced in the existing concurrent atomic
writer test (7 failures in 12 repetitions): simultaneous replacement of the same
target can briefly return Windows access/sharing errors. Atomic output now
retries WinError 5, 32, or 33 up to five times with 10/20/40/80 ms delays. Each
attempt revalidates source-file protection. It retains the same complete
temporary snapshot, never deletes the target or writes it in place, and still
reports persistent failures. Added regressions cover both transient recovery
and bounded failure preserving the previous file. The concurrency barrier
synchronizes each writer's first attempt while allowing later retries.

The first packaged self-check encountered an intermittent Tesseract `--version`
exit status `0xC0000142`. It was reproduced at the exact self-check boundary;
subsequent default/clean-DLL-path comparisons also succeeded, so DLL-path
pollution is not established as the cause. Tesseract subprocesses now launch
without a console window; version probes have a ten-second timeout and retry
that specific native initialization status once after 50 ms. Other errors and
persistent failures remain failures. The adapter changes pytesseract's local
subprocess reference, not the shared subprocess module or DLL search settings.
Self-check exceptions now write an `ok: false` report and exit instead of
waiting in a PyInstaller exception dialog. These are bounded launch/failure
handling changes, not a claim that the underlying intermittent Windows loader
condition has been conclusively identified.
