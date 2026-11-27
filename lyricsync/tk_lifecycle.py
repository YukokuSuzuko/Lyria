"""Shared final teardown for an application's complete Tk interpreter."""


def destroy_root(root):
    """Cancel timers without deleting commands owned by child widgets.

    Tkinter's root.after_cancel() also unregisters a Python command, even when
    another widget registered it. That widget would delete it a second time
    during destroy(). Cancel only the Tcl timers here; each widget retains its
    command registrations until root.destroy() disposes of the widget tree.
    This function is for final teardown, not pausing an active application.
    """
    for job in root.tk.call('after', 'info'):
        root.tk.call('after', 'cancel', job)
    root.destroy()
