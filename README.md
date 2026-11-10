# UniXNU-uke

> **Superseded.** This repository is the **previous** device repository for the Xiaomi Pad 7 / POCO Pad
> X1 (`uke`, SM7675) — the iPadOS 17.7.11 (21H461) line. It is kept as history and reference. The
> current device repository is **[`TongHuaQWQ/macOS-ForUke`](https://github.com/TongHuaQWQ/macOS-ForUke)**,
> which targets macOS 26.5.2 (25F84), carries the working boot chain and the log writers, and holds the
> `porting.toml` that is maintained today. Start there; read this one for how the port got here.

## 1. What this is

A UniXNU format-2 device repository for `uke`: `board.toml` (component selection, the iPad7,1 iPadOS
17.7.11 restore image, userdata geometry, payload and ADT values), the kernel defconfigs under
`configs/`, the shared patch series under `patches/xnu/`, the board copies of the pinned drivers, the
kernel-side packages in `package/` (linked as `kits/uke`), and the GitHub Actions workflows that build
the kernel.

It is the **device** layer of the boot chain, build-time only: what it produces is the resolved kernel
configuration and archive list, the kernel artifact from CI, the payload and the boot image. Boot chain
on the tablet: `fastboot` → ABL → UEFI (`boot_a`) → `UniXNU.efi` → DarwinBoot → XNU (payload in
`vendor_boot_a`).

What it reached, and where it stopped: stages 1–5 work on the stock ABL (UEFI firmware, the UniXNU
menu, the payload, DarwinBoot, the XNU entry — the green bar). After the green bar XNU waits for the
root device; `SecurityLock … LockCapsuleInterface` is UEFI output, not the kernel hang.
`uke-full_defconfig` compiles the 4 KiB XNU with source corecrypto, pthread, NativeUfs, NativeAES,
mockfs, PMGR, the public storage headers and the GOP legacy display, and prelinks it — but the
storage/root runtime path was never verified on the device. Progress, remaining work and the update
rule: **[docs/PROGRESS.md](docs/PROGRESS.md)**.

Format 2: the board pins the sm7675 SoC layer in `feeds.toml` and the `ipados-17.7.11-21H461` OS
profile; `porting.toml` records the state of each area and `AGENTS.md` has the editing rules.

## 2. Where it sits

Fork chain: `https://github.com/Allenwdk/UniXNU-uke` (this checkout's `origin`, the parent) →
`TongHuaQWQ/UniXNU-uke` (remote `fork`, branch `main`, 13 commits ahead of `origin/main`).

| Relationship | What | Where |
| --- | --- | --- |
| consumes | core `Allenwdk/UniXNU`, ref `uke-gpu` | `.github/workflows/build-kernel.yml` (both jobs); the `core_repository` / `core_ref` inputs default to the same pair in `.github/workflows/build-kernel-min.yml` |
| pins | `sm7675` SoC layer, `https://github.com/Allenwdk/UniXNU-sm7675-common` @ `98618c197e789acfd7ac6755ad23bf7e04881780`, plus `local = "../UniXNU-sm7675-common"` | `feeds.toml` `[[source]] name = "sm7675"` (`kind = "soc"`) |
| pins | `Adreno740` (`Allenwdk/NativeAdreno`), `GopFramebuffer` (`Allenwdk/NativeMobileDisplay`), `AdrenoMetal` | `feeds.toml` (`kind = "driver"`, with `local` overrides) |
| pins | board copies `ShengUfs` → `drivers/NativeUfs`, `ShengNovatekTouch` → `drivers/NativeNovatekTouch` | `feeds.toml` (`local = …`, no git pin) |
| superseded by | `TongHuaQWQ/macOS-ForUke` (macOS 26.5.2 line) | — |

Nothing pins this repository any more. The core checkout it names (`Allenwdk/UniXNU@uke-gpu`) is the
predecessor of the `TongHuaQWQ/UniXNU@macos` branch the current device repository uses; the component
kits it relied on by script name now come from the core's catalog (`components.toml`) and its
submodules (`drivers/ArmCpu`, `drivers/DarwinBoot`, `kits/XnuPrelink`).

## 3. Dependencies

| Input | Comes from | Without it |
| --- | --- | --- |
| macOS host with Xcode + `iphoneos` SDK (or a GitHub macOS runner) | App Store / developer.apple.com; `runs-on: macos-latest` in the kernel workflows | The XNU build cannot run. The firmware, payload and boot-image steps run on Linux. |
| `git`, `make`, `clang`, `cmake`, `ninja`, `unifdef`, `mtools`, `dtc`, `python3` | Xcode command-line tools / Homebrew | `./unixnu` stops at its tool check. |
| core `Allenwdk/UniXNU@uke-gpu` | GitHub, checked out as `UniXNU/` beside this repository | No `./unixnu`; nothing resolves. |
| `UniXNU-sm7675-common` (`98618c1…`) | `https://github.com/Allenwdk/UniXNU-sm7675-common`, or the sibling `../UniXNU-sm7675-common` named by the `local` key | `soc sm7675: … is not a directory` on a clean runner. |
| iPadOS 17.7.11 (21H461) restore image for iPad7,1 | `[ipsw] url` (Apple CDN) in `board.toml`; `--ipsw` / `UNIXNU_IPSW` overrides | No kernelcache to prelink against, and no OS/SystemOS members for the userdata image. |
| `uefi-uke` UEFI tree (Mu-Silicium fork) | **Not published** as a repository; the port's firmware-layer changes are carried alongside the device work | No firmware volume, so no payload volume and no boot image. |
| Platform seed (`--unique-chip-id`) | Read from the device | The payload cannot be built. |
| userdata image tooling (`hdiutil`) | macOS only | The userdata image cannot be produced; a Linux path (`qemu-img convert -f dmg -O raw` plus the existing `SHNGROOT` writer) is possible but was never implemented here. |
| A732 GPU firmware from the vendor partition | The tablet's `/vendor/firmware_mnt/image` and `/odm/firmware`, restored from the `uke-gpu-firmware-*` Actions cache | GPU configurations build with weak empty images and the GMU refuses at runtime; `uke-full-plus_defconfig` is not built by CI for this and other reasons. |

Missing against the specification: `scripts/package.sh` and `.github/workflows/package.yml`, required of
every device repository, are **not present here** either.

## 4. Build and use

Firmware (Linux), from a UniXNU checkout with this repository beside it:

```sh
cd /path/to/UniXNU
./unixnu defconfig uke_defconfig -D ../UniXNU-uke
./unixnu fetch -D ../UniXNU-uke
./unixnu build firmware --skip-preflight -D ../UniXNU-uke --uefi ../uefi-uke -j 8
./unixnu bootimg -D ../UniXNU-uke --uefi ../uefi-uke \
    --dtb ../uke-port/data/vendor_boot.dtb --ramdisk none
```

The firmware is `uefi-uke/Build/ukePkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd` (3 MiB); the UEFI-only boot
image is `out/uke/boot/boot.img` (Android v4, ~1.5 MiB). The UEFI build uses `UKE_VENDOR_HW = 0`: Mu
drivers, `SimpleFbDxe`, the UniXNU boot manager and DarwinBoot; no uke vendor UEFI drivers.

Kernel (GitHub Actions), then the payload and boot image:

```sh
gh workflow run build-uke-kernel          # or build-uke-kernel-min, the self-diagnosing variant
# download the uke-kernel artifact, then:
./unixnu defconfig uke_defconfig -D ../UniXNU-uke
./unixnu build payload -D ../UniXNU-uke --kernel /path/to/kernel.macho --unique-chip-id <hex>
./unixnu bootimg -D ../UniXNU-uke --uefi ../uefi-uke --dtb /path/to/vendor_boot.dtb
```

Flashing (`fastboot boot` is rejected by this ABL: `Failed to load/authenticate boot image: Load Error`):

```sh
adb reboot bootloader
fastboot flash boot out/uke/boot/boot.img
fastboot flash vendor_boot /path/to/vendor_boot_uke_full.img
fastboot set_active a
fastboot reboot
```

Restore Android with the stock `boot_a.img` / `vendor_boot_a.img` the same way. Use
`fastboot set_active a` before each test: the ABL marks the slot unbootable after repeated non-Android
boots. If the firmware stalls, retry the same image once.

CI has two kernel workflows: `build-uke-kernel` (first console: BootShim/UEFI, UniXNU, the payload
`xnu` entry, DarwinBoot, then XNU with the GOP legacy display) and `build-uke-kernel-min` (the same
chain, format-2 aware). Both reach the XNU entry (green bar); the full kernel can publish a UFS block
device, but the device was never booted with it. Verified-root/owner-root, VirtIO/RamBlock and boot
progress stay off until their live audit inputs exist.

## 5. See also

- [Port a device](https://unixnu.com/zh/porting/steps) — the eight steps this repository was built
  against; [migration](https://unixnu.com/zh/porting/migration) — the format-2 move;
- [Device repository](https://unixnu.com/zh/porting/device-repository) — the file list and completion
  checklist; [packaging](https://unixnu.com/zh/porting/packaging), [drivers](https://unixnu.com/zh/porting/drivers),
  [UEFI firmware](https://unixnu.com/zh/porting/uefi-firmware), [log capture](https://unixnu.com/zh/porting/log-capture);
- [Build and boot](https://unixnu.com/zh/building), [build environment](https://unixnu.com/zh/guide/requirements);
- this repository: `docs/PROGRESS.md` (state and update rule), `KERNEL-BUILD.md` (CI notes),
  `docs/MACOS-BUILD.md` (manual macOS guide), `docs/GPU-PORT.md`, `docs/DPU-PORT.md`,
  `docs/TOUCH-PORT.md`, `docs/GPU-SHENG-COMPARE.md`;
- device records: `porting.toml` (per-area status, inputs, evidence) and `board.toml`;
- successor: [`TongHuaQWQ/macOS-ForUke`](https://github.com/TongHuaQWQ/macOS-ForUke) — macOS 26.5.2
  (25F84), the maintained port.

## Layout

- `board.toml` — board description, IPSW identity, userdata geometry, payload and ADT settings.
- `configs/uke_defconfig` — first-console kernel: 4 KiB XNU, source corecrypto, `ArmDTPlatform`,
  `ArmCpu`, arch timer, PAN, panic region and the GOP legacy display. No UFS, AES, VirtIO, RamBlock,
  PMGR, Adreno or owner-root stack.
- `configs/uke-full_defconfig` — the reproducible stage-6 full configuration: corecrypto, pthread,
  NativeUfs (bring-up transport), NativeAES with the recorded original bindings, storage headers,
  mockfs, PMGR, arch timer, PAN, panic region and the GOP legacy display.
  `CONFIG_XNU_BUILD_ARCHIVES=y` runs the board archive list.
- `configs/uke-full-plus_defconfig` — sheng parity: the full set plus the storage ABI probes, verified
  root, VirtIO/RamBlock and owner-root. It needs live storage metadata, signed policy bundles and the
  Adreno 732 firmware, so it is not built by CI.
- `configs/uke-full-minus-pmgr_defconfig` — PMGR + legacy display only; used to show that PMGR is not
  the root-device blocker.
- `configs/uke-gpu_defconfig` — the stage-6 full set plus the A732 NativeAdreno profile, GMU/vote
  headers and the GPU power probe. It builds without vendor firmware (weak empty images; the GMU
  refuses at runtime) and is the configuration for a first GPU bring-up.
- `configs/uke-gpu-userland_defconfig` — the userland/Data/union configuration plus NativeAdreno, used
  for the actual device GPU probe (the probe runs in the display/userland path). The private gen71100
  firmware is restored from the `uke-gpu-firmware-*` Actions cache; see `docs/GPU-PORT.md`.
- `configs/uke-storage_defconfig`, `configs/uke-userland_defconfig`, `configs/uke-nodisplay_defconfig`,
  `configs/uke-cpu_defconfig`, `configs/bootstrap_defconfig` — the paths those configurations cover
  (`bootstrap_defconfig` is the no-device-archive pass: compile records plus the prelinked candidate
  the legacy display ABI builder and the UFS binder need).
- `docs/PROGRESS.md` — bring-up stage, completed work, remaining work, artifacts, update rule.
- `docs/GPU-PORT.md` — Adreno 732 values, the A740/A732 differences and the firmware the vendor
  partition must supply.
- `scripts/build-xnu-4k-baseline.sh` — base script plus a `native_legacy_display_probe` logging stub
  used only when `NATIVE_LEGACY_DISPLAY` is off (the bootstrap pass).
- `scripts/make-uke-adt.py` — XNU Apple device tree generator. GIC, UFS, timebase and DRAM base are
  the values read from the live stock DT; the root compatible is `xiaomi,uke` / `qcom,sm7675`.
- `scripts/check-uke.py` — compares the live DT reserved-memory ranges with
  `uefi-uke/Platforms/Xiaomi/ukePkg/Library/MemoryMapLib/MemoryMapLib.c`.
- `drivers/NativeNovatekTouch` — NT36532 protocol (from the sheng port).
- `package/` — kernel-side probes/kits copied from the sheng port; the device package is linked as
  `kits/uke`.
- `KERNEL-BUILD.md` — the CI build notes; `docs/MACOS-BUILD.md` is the manual macOS guide.
- `.github/workflows/build-kernel.yml` — the CI version of `docs/MACOS-BUILD.md`: bootstrap → cached
  archive chain → first-console or full kernel + prelink. The `full` job uses the board archive list.

## Hardware data captured from the device

- Device: `2410CRP4CC`, 12 GB RAM, 256 GB Samsung UFS, Android 17 (`OS4.0.4.0.XPYCNXM`), slot `_a`.
- `userdata`: `/dev/block/sda32`, first 4 KiB LBA `0x2d3000`, last `0x3b147fa` (225 GiB).
- `splash_region`: `0xe3940000` + `0x2b00000` (the live DT; sheng's `0xb8000000` is not valid here).
- UFS: `0x1d84000`, compatible `qcom,ufshc`, stream ID `0x60`.
- Touch: NT36532 on GENI SPI `0xa90000`, IRQ GPIO 54, lcd_id GPIO 26, 19.2 MHz.
- Panel: O82, selected by `msm_drm.dsi_display0=qcom,mdss_dsi_o82_42_02_0b_dualdsi_dsc_vid:`.
- Stock boot: Android header v4, 4096-byte pages, 96 MiB partition.

## Known gaps

- `bootimg.py` now understands Android boot v4; the stock uke image is v4.
- `MemoryMapLib.c` is generated from the live stock DT plus the Mu-Silicium FD/stack layout.
  `check-uke.py` reports 0 static errors (72 descriptors).
- `UkeBootMemoryDxe` publishes the initrd after rounding its end down to a 512-byte sector, maps the
  initrd range in the GCD, and adds up to 2 GiB of DRAM. The ADT uses
  `owned_memory_size = 0x10000000` because the largest contiguous free run on uke is about 326 MiB.
- Stages 1–5 are reached (XNU entry, green bar). Stage 6 compiles: the full kernel links NativeUfs and
  NativeAES, and the UFS block class binds to the pinned original candidate. Runtime storage/root is
  untested; the device is not connected.
- The UFS block reads are **not authenticated** in the bring-up build. The verified-root policy bundle
  (built from the real OS/SystemOS DMGs) and the live storage ABI audit are the remaining gates for
  authenticated reads.
- `userdata`, the root UUID and the APFS/launchd chain still need a device run.
- `uke-full-plus_defconfig` lists the sheng-parity flags that are not built: VirtIO/RamBlock, verified
  root, owner-root and boot progress.
- The uke SM7675 GCC UFS block is at `0x177000`/`0x19e000`; the device overrides `drivers/NativeUfs`
  with those offsets.
- The vendor UFS/clock/PMIC stack (`UKE_VENDOR_HW = 1`) is not imported.
- `panic_screen = false` because the panic-screen builder needs `Font.h`; enable it after fixing the
  include path so a real panic is captured.
- The touch firmware object is empty, so the NT36532 probe stops before the download stage.
- The userdata image needs macOS `hdiutil`; a Linux port using `qemu-img convert -f dmg -O raw` plus
  the existing `SHNGROOT` writer is possible but not implemented here.
- GPU: the Adreno 732 values are recorded and checked; the driver still needs the A732 firmware from
  the vendor partition and the GPU power path. See `docs/GPU-PORT.md`.
- USB, audio, Wi-Fi and sensors are not part of this pass.
- No `scripts/package.sh` / `.github/workflows/package.yml`, which the device-repository
  specification requires.

See [docs/PROGRESS.md](docs/PROGRESS.md) for the full state, artifacts and maintenance rule.
