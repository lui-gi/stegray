# stegray

A command-line steganalysis toolkit. Point it at a file and it runs the usual
stego tools in parallel, prints what each one found, and keeps anything they
extracted.

```console
$ stegray challenge.png
stegray 0.1.0
file: challenge.png
type: image/png
output: challenge.png.stegray/

file
│ PNG image data, 600 x 337, 8-bit/color RGBA, non-interlaced

binwalk  ★ extracted 1 file
│ DECIMAL  HEXADECIMAL  DESCRIPTION
│ 0        0x0          PNG image, total size: 300538 bytes
│ 300538   0x495FA      ZIP archive, file count: 1, total size: 197 bytes
╰ → binwalk/challenge.png.extracted/495FA/carved.txt

· foremost   nothing found
· openstego  nothing found
...

summary
│ found    binwalk (1 file), pcrt (1 file)
│ nothing  foremost, openstego
╰ skipped  pdfinfo, pdfid, steghide, jsteg (file type)
```

## Analyzers

| Analyzer | Runs on | What it does |
| --- | --- | --- |
| [file](https://manned.org/file.1) | any file | MIME type and format detection |
| [pdfinfo](https://poppler.freedesktop.org/) | PDF | document metadata |
| [pdfid](https://blog.didierstevens.com/programs/pdf-tools/) | PDF | counts of risky objects (`/JS`, `/OpenAction`, …) |
| [exiftool](https://exiftool.org/) | any file | metadata and geolocation |
| [binwalk](https://github.com/ReFirmLabs/binwalk) | any file | embedded files, extracted (v2 and v3 both supported) |
| [foremost](https://foremost.sourceforge.net/) | any file | carved files |
| [outguess](https://www.rbcafe.com/software/outguess/) | JPEG | extraction, with password (`--deep` only) |
| [pngcheck](https://www.libpng.org/pub/png/apps/pngcheck.html) | PNG | chunk listing and validation |
| pcrt (built in) | PNG | header/IHDR/IDAT/IEND check and repair, data after `IEND` |
| [identify](http://www.graphicsmagick.org/identify.html) | images | image properties |
| [steghide](https://steghide.sourceforge.net/) | JPEG, BMP, WAV, AU | extraction, with password |
| [jpseek](https://github.com/h3xx/jphs) | JPEG | jphide extraction, with password (also needs `expect`) |
| [jsteg](https://github.com/lukechampine/jsteg) | JPEG | LSB extraction |
| [openstego](https://www.openstego.com/) | images | extraction, with password |
| [zsteg](https://github.com/zed-0xff/zsteg) | PNG | LSB text/data detection |
| [strings](https://pubs.opengroup.org/onlinepubs/9799919799/utilities/strings.html) | any file | printable strings |

An analyzer is skipped when the file is not a type it handles or when its tool
is not installed; the report ends with the list of what was skipped and why.

## Install

stegray itself is pure Python (3.12+) with no dependencies:

```sh
pipx install .        # or: pip install .
stegray --list-tools  # shows which of the external tools are on your PATH
```

It uses whichever of the tools above you have installed. To install them on
the host, run the script from a checkout:

```sh
./install-tools.sh            # --dry-run shows what it would do
```

It detects the OS and package manager (apt, dnf, pacman, zypper, or Homebrew
on macOS), installs what the distribution packages (using `sudo`), and builds
or downloads the rest into `~/.local/bin` (change with `--prefix`). Tools
already on your PATH are left alone. steghide is not packaged for Arch (it is
in the AUR) or Homebrew, so it stays missing there.

On Windows, use the PowerShell script instead:

```powershell
powershell -ExecutionPolicy Bypass -File install-tools.ps1   # -DryRun to preview
```

It installs what [Scoop](https://scoop.sh) packages (offering to install Scoop
first if it is missing) and downloads steghide, jsteg and OpenStego into
`%LOCALAPPDATA%\stegray-tools`, which it adds to your user PATH. binwalk,
foremost, pngcheck, outguess and jpseek have no native Windows build; for
those, run `install-tools.sh` inside WSL or use the container image.

To get all of them without installing anything on the host, build the
container image instead:

```sh
podman build -t stegray .   # or docker build
podman run --rm -v "$PWD:/work:z" stegray challenge.png -p hunter2
```

The image's working directory is `/work`, so mount the directory holding the
file there; the output folder appears next to it. With Docker, add
`--user "$(id -u):$(id -g)"` so the results are owned by you rather than root.

## Usage

```
stegray [options] FILE

  -p, --password PASSWORD  password for the tools that take one
  -d, --deep               also run the slow analyzers (outguess)
  -o, --output DIR         where to keep extracted files and results.json
                           (default: ./<file>.stegray)
  --only TOOLS             comma-separated analyzers to run, instead of all
  --skip TOOLS             comma-separated analyzers to leave out
  --timeout SECONDS        time limit per tool invocation (default: 300)
  --json                   print the results as JSON
  --full                   never shorten long tool output on a terminal
  --no-color               disable colored output
  --list-tools             list the analyzers and whether their tools are installed
```

Each tool's section shows one of four outcomes: `★` when it extracted files or
flagged something, its plain output when it only has information to report,
`· nothing found` when it ran and came up empty (a wrong steghide password, for
instance), and `✗ failed` when it crashed or timed out. A closing summary lists
the tools by outcome, along with the ones that were skipped. In `results.json`
these are the statuses `ok`, `error` (nothing found) and `failed`.

On a terminal, long output such as `strings` is cut to 40 lines per tool and a
tool that found nothing is reduced to one line; pass `--full` to see it all. When the output is piped nothing is cut, so
`stegray file.png | grep -i flag` searches everything.

### Output folder

```
challenge.png.stegray/
├── results.json      everything in the report, machine-readable
├── binwalk/          files each tool extracted, one folder per tool
└── steghide/
```

Running stegray again on the same file replaces the previous results. It
refuses to write into a non-empty folder it did not create.

## Adding an analyzer

Create one file in `stegray/analyzers/` that subclasses `SubprocessAnalyzer`
and sets a few class attributes (`name`, `accepts`, `has_archive`,
`needs_password`, `deep_only`, `display_order`); it is discovered
automatically. Then install its tool in the `Dockerfile` and in
`install-tools.sh` (and `install-tools.ps1` if it runs on Windows).

## Development

```sh
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
```

Tests that need an external tool are skipped when it is missing. To run all of
them, use the image, which has every tool:

```sh
podman run --rm -v "$PWD:/src:ro,z" --entrypoint sh stegray -c \
  'cp -r /src /tmp/s && cd /tmp/s && pip install -q pytest && python -m pytest -p no:cacheprovider'
```

## License

MIT
