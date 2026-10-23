# Third-party components

This project does not vendor third-party binaries. Dependencies are installed from PyPI at setup time.

- [`py-scrcpy-sdk`](https://pypi.org/project/py-scrcpy-sdk/) 0.1.2 — MIT License. Provides the Python interface to ADB/scrcpy video and touch control. Its package includes the scrcpy server component; see its distribution for notices and license terms.
- [`numpy`](https://pypi.org/project/numpy/) — BSD 3-Clause License.
- Transitive dependencies are installed as declared by `py-scrcpy-sdk`; each retains its own license and notices.
- MuMu Player Pro, MuMu Player, Android Debug Bridge, and scrcpy are separate products/projects. They are not included in this repository and remain subject to their own licenses and terms.
