# m2a

Export commit message bodies (mail bodies) from a git repository via `git format-patch`, keep only `Date` + body, and merge them into size-limited `.txt` files.

## Breaking Change

This project no longer walks history by `git reset --hard`. There is no migration: direct replacement.

## Prerequisites

- Bash (optional wrapper)
- Python 3
- Git

## Usage

### Step 1: Download Kernel Mailing List Repository from EROL

First, clone the target kernel mailing list repository from [https://erol.kernel.org/](https://erol.kernel.org/). EROL hosts repositories for various kernel mailing lists (e.g., `linux-mm`, `linux-kernel`, `qemu-devel`).

#### Example: Clone the `linux-mm` Mailing List Repository
```bash
# Clone the linux-mm mailing list repo to local directory "linux-mm-repo"
git clone https://erol.kernel.org/linux-mm/git/0 linux-mm-0-repo
```

Notes on EROL Repositories:

- Each EROL repository contains a core source file (typically named m) that stores the raw email data.
- Ensure the cloned repository has the m file (located in the root of the repo directory) before running the script.

### Step 2: Inspect the selectable range (by date)

```bash
./m2a.sh list --repo /path/to/repo --since 2025-01-01 --until 2025-02-01
```

### Step 3: Export as `date-part.txt`

```bash
./m2a.sh export --repo /path/to/repo --since 2025-01-01 --until 2025-02-01 -o ./out --max-mib 64
```

Output files are named as:

`YYYY-MM-DD-1.txt`, `YYYY-MM-DD-2.txt`, ...

The `YYYY-MM-DD` prefix defaults to the first commit date in the selected range (override via `--prefix-date`).

Each record is written as:

`YYYY-MM-DD` + `Subject` + commit message body + patch diff.

Implementation detail: `m2a` generates `.patch` files via `git format-patch` and filters each patch using `format.py`.
