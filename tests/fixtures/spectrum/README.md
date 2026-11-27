# Immutable dev3 spectrum reference

`AudioAnalysisPanel.dev3.qml` is an exact byte copy of `desktop_qt/qml/AudioAnalysisPanel.qml` from
Lyria dev3 commit `07241ae148ee9b34c79368835e16b5d9a3dd99f6`.

SHA-256: `ad17da95ef970dc877702ed9073f9079a8ace594d631ce4ba9d35bd87e6c9776`

This read-only test reference is never edited or used as product QML. The
same-media comparison checks the hash and renders these bytes with the current
QML support components. It does not require Git or a `.git` directory, and does
not claim to run the old complete executable or Aegisub.

New spectrum tests write output to each test's `tmp_path/evidence` by default.
Set `LYRIA_SPECTRUM_EVIDENCE` to collect outputs in an explicitly chosen directory;
the tests create that directory. Existing historical reports are not overwritten
by default. When an explicit directory is reused, its same-named artifacts may
be replaced by that requested run.
