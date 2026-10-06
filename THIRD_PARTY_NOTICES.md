# Filmocity third-party components

Filmocity's original source is under the MIT terms in [LICENSE](LICENSE).
That license does not replace the licenses of separately distributed components.

## Windows Python environment

The reviewed RC6 environment uses **standard, GIL-enabled CPython 3.13.16 Windows x64**.
Its finite Windows acceptance scope is recorded in [release readiness](docs/RELEASE_READINESS.md).
Launch/build guards require a standard 3.13 patch at least 16; allowing a later
patch does not qualify it automatically. Exact runtime, build and test
package versions and archive hashes are in `requirements-*-windows.lock` and
[packaging/dependencies-windows.json](packaging/dependencies-windows.json).
The inventory includes build/test tools; their presence there does not mean
every tool is embedded in the executable. Optional speech packages are outside
this locked environment and are not included in this Windows candidate.

The selected 34 package versions remain unchanged. Four native archives—cffi,
Pillow, pydantic-core and websockets—now use cp313 Windows x64 wheels with new
hashes. The official interpreter installer hash and valid Python Software
Foundation signature, fresh offline hash-locked installation and `pip check`
were recorded locally. The measured interpreter is 3.13.16 with GIL enabled,
Expat 2.8.5 and OpenSSL 3.5.9. These are interpreter/environment measurements,
not an executable SBOM, comprehensive security clearance or native RC acceptance.

[Python 3.13.16](https://www.python.org/downloads/release/python-31316/) is the
final full-maintenance release with regular binary installers. Subsequent 3.13
security releases are source-only under [PEP 719](https://peps.python.org/pep-0719/).
Future security updates need a planned 3.14 qualification or controlled source
build. Earlier 3.12 installation/build/test receipts remain historical.

Exact license/notice files from the selected archives are preserved in
[licenses](licenses), together with `licenses/Python-LICENSE.txt`. Preserve
these notices with source and binary distributions. PyInstaller's exception,
the Pillow dependency notices and Python's bundled-component notices remain
part of their respective license files.

`proxy-tools` 0.1.0's archive declares MIT in package metadata but omits its
license text. The upstream project publishes BSD terms with Armin Ronacher and
Jonathan Tushman attribution. Both that discrepancy and the retrieved upstream
notice are retained; see `licenses/proxy-tools/UPSTREAM-LICENSE.txt` and
[the upstream file](https://github.com/jtushman/proxy_tools/blob/master/LICENSE.txt).

The pywebview wheel contains Microsoft WebView2 SDK libraries. The Core DLL
was byte-matched to official NuGet package **Microsoft.Web.WebView2 1.0.3856.49**.
Its license, third-party notice and package metadata are retained in
`licenses/microsoft-webview2`. The installed Windows WebView2 runtime is a
separate Microsoft component; it is not installed or redistributed by this
source repository.

Bundled DejaVu fonts retain [their license](assets/fonts/LICENSE-DejaVu.txt).

## FFmpeg and ffprobe

The source repository excludes tool executables. Supply a reviewed FFmpeg
distribution in `bin/` or on PATH for source use. The bootstrap no longer
downloads a floating `latest` binary or discards an upstream distribution's
notices. Windows candidate packaging embeds the explicitly selected tools and
captures their hashes, version strings, adjacent notices and DLLs separately.

The currently preserved local tools identify as
**N-126435-gf93cd72dde-20260906**, configured with `--enable-gpl --enable-version3`.
Their own license output identifies **GPL version 3 or later**. They are not
covered by Filmocity's MIT license. Local validation of these exact binaries
does not establish public redistribution readiness.

**Public distribution of a candidate containing these tools remains blocked**
until the matching complete corresponding source, build scripts and required
third-party notices are retained and made available with a reviewed release
distribution method. A link to current FFmpeg source or to a floating binary
download is not evidence for the complete corresponding source of this build.
Do not claim that adding a license text alone closes this requirement.

See [FFmpeg's licensing guidance](https://www.ffmpeg.org/legal.html) and
[the build provider's package and retention documentation](https://github.com/BtbN/FFmpeg-Builds).
The old source transfer, installed executable and backups remain preserved for
local testing; the clean GitHub source candidate does not include those binaries.

## Dependency advisory scope

On 2026-10-05 the 34 selected Python archive hashes matched PyPI's per-version
metadata; none was yanked or had a listed vulnerability in that response.
This is a time-bound check of published advisories, not a proof that software
has no defects. It does not cover FFmpeg's native libraries, drivers, Windows,
or a different dependency set. Repeat the check before publication and retain
the resulting report with that release's evidence.
