#!/usr/bin/env python3
"""Link the USB device stack on its own: QcomDwc3 + AppleUSBXDCI/XHCI, without the display.

Why this file exists
--------------------
The board builds its USB objects inside scripts/build-native-legacy-display-27.py,
in the "USB device mode" section of that script (its fixture loading, the
OriginalUsbXdciABI.h / OriginalUsbXhciABI.h generation, the per-slot vtable
verification against scripts/usb-xdci-24A446.json and scripts/usb-xhci-24A446.json,
the usb-bindings.S / usb-addresses.S emission and the three compile calls).
That script also builds the legacy display, the touch driver and the Adreno
kits, and those halves need firmware caches and ABI observation inputs staged by
the board workflow (run 44677633586 stopped in build-adreno740-kits on a missing
ShengGmuSource.h). None of that is on the DWC3 path.

So this file is the USB section, extracted whole: same flags, same generated
headers, same verification, same binding emission. Nothing about USB is
re-derived here. The display-side symbols UkeUsbBoard.cpp calls (it contains the
DP-AUX/DPU route) get weak, fail-closed fallbacks when the display archive is
absent, the same way the board stubs its storage adapters
(package/UfsDpuStubs.c).

Output: build/uke-usb/libUkeUsb.a + build/uke-usb/build-report.json.
Selected by configs/uke_usb_defconfig through CONFIG_XNU_EXTRA_LD_ARCHIVES.

  python3 scripts/build-uke-usb.py           # build the archive
  python3 scripts/build-uke-usb.py --check   # fixtures + generated headers only
"""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from kernel_target import ARCH, IOS_VERSION, TRIPLE

if ARCH != 'arm64e' or IOS_VERSION.split('.')[0] != '27':
    raise SystemExit('This USB builder only supports arm64e iPadOS 27 (24A446).')

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/uke-usb'
OUT.mkdir(parents=True, exist_ok=True)
KITS = ROOT / 'kits/uke'
remoteproc = ROOT / 'kits/Sm7675Platform/remoteproc'
llvm = Path(os.environ.get('UNIXNU_TOOLCHAIN', '/opt/unixnu-linux')) / 'llvm/bin'
USB = ROOT / 'drivers/QcomDwc3'
DISPLAY_ARCHIVE = ROOT / 'build/native-legacy-display/libNativeLegacyDisplay.late.a'
CHECK_ONLY = '--check' in sys.argv[1:]

# --- helpers, copied verbatim from scripts/build-native-legacy-display-27.py ---
def run(cmd, **kw):
    return subprocess.check_output([str(x) for x in cmd], timeout=300, **kw)

def require(v, why):
    if not v:
        raise SystemExit(why)

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def discriminator(name):
    """clang ptrauth_string_discriminator: SipHash-2-4 with the fixed key, modulo 0xffff, plus one."""
    mask = (1 << 64) - 1
    rotl = lambda x, b: ((x << b) | (x >> (64 - b))) & mask  # noqa: E731
    key = bytes([0xb5, 0xd4, 0xc9, 0xeb, 0x79, 0x10, 0x4a, 0x79, 0x6f, 0xec, 0x8b, 0x1b, 0x42, 0x87, 0x81, 0xd4])
    k0, k1 = struct.unpack('<2Q', key)
    v = [0x736f6d6570736575 ^ k0, 0x646f72616e646f6d ^ k1, 0x6c7967656e657261 ^ k0, 0x7465646279746573 ^ k1]

    def rounds(n):
        for _ in range(n):
            v[0] = (v[0] + v[1]) & mask; v[1] = rotl(v[1], 13) ^ v[0]; v[0] = rotl(v[0], 32)
            v[2] = (v[2] + v[3]) & mask; v[3] = rotl(v[3], 16) ^ v[2]
            v[0] = (v[0] + v[3]) & mask; v[3] = rotl(v[3], 21) ^ v[0]
            v[2] = (v[2] + v[1]) & mask; v[1] = rotl(v[1], 17) ^ v[2]; v[2] = rotl(v[2], 32)
    data = name.encode()
    full = len(data) & ~7
    for i in range(0, full, 8):
        m = struct.unpack_from('<Q', data, i)[0]
        v[3] ^= m; rounds(2); v[0] ^= m
    b = len(data) << 56 & mask
    for i, c in enumerate(data[full:]):
        b |= c << (8 * i)
    v[3] ^= b; rounds(2); v[0] ^= b; v[2] ^= 0xff; rounds(4)
    return (v[0] ^ v[1] ^ v[2] ^ v[3]) % 0xffff + 1

def macho(data):
    """Load commands, segments and sections of an arm64e relocatable object."""
    magic, cpu, _, _, count = struct.unpack_from('<5I', data)
    require(magic == 0xfeedfacf and cpu == 0x100000c, 'arm64 Mach-O object')
    commands, segments, offset = [], [], 32
    for _ in range(count):
        command, size = struct.unpack_from('<2I', data, offset)
        commands.append((command, offset, size))
        if command == 0x19:
            nsects = struct.unpack_from('<I', data, offset + 64)[0]
            sections = []
            for i in range(nsects):
                at = offset + 72 + 80 * i
                va, length, fileoff = struct.unpack_from('<QQI', data, at + 32)
                sections.append({'name': data[at:at + 16].rstrip(b'\0').decode(), 'va': va, 'size': length,
                                 'fileoff': fileoff, 'command_offset': at})
            segments.append({'sections': sections})
        offset += size
    return {'commands': commands, 'segments': segments}

def run_compile(cmd, cwd, name):
    """Compile one probe; the assertion/build output of a failing compiler goes to the log."""
    result = subprocess.run([str(x) for x in cmd], cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        log = OUT / (name + '.compile.log')
        log.write_text(result.stdout)
        raise SystemExit(f'{name}: compile failed\n{result.stdout[-6000:]}')

def compile_cpp(src, dst, extra=()):
    cmd, args, i = [], r['arguments'], 0
    while i < len(args):
        value = args[i]
        if value == r['file']:
            cmd.append(str(src))
        elif value == '-o':
            cmd += ['-o', str(dst)]
            i += 1
        elif value in ('-MF', '-MT', '-MJ'):
            i += 1
        elif value == '-Xclang' and args[i + 1].startswith('-W'):
            i += 1
        elif not value.startswith('-W'):
            cmd.append(value)
        i += 1
    cmd += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unnecessary-virtual-specifier', '-Wno-c99-designator',
            '-Wno-reorder-init-list', '-Wno-deprecated-enum-enum-conversion', '-I', str(ROOT / '.cache/xnu-work/osfmk'), *extra]
    run_compile(cmd, r['directory'], Path(dst).stem)

def compile_c(src, dst, recipe='osfmk/DEVELOPMENT/startup.o.json'):
    """A C board core with the flags of one kernel C file (osfmk for the cores, bsd for the VFS readers)."""
    rc = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837' / recipe).read_text().strip().rstrip(','))
    cmd, args, i = [], rc['arguments'], 0
    while i < len(args):
        value = args[i]
        if value == rc['file']:
            cmd.append(str(src))
        elif value in ('-o', '-MF', '-MT', '-MJ'):
            if value == '-o':
                cmd += ['-o', str(dst)]
            i += 1
        elif value == '-Xclang' and args[i + 1].startswith('-W'):
            i += 1
        elif not value.startswith('-W'):
            cmd.append(value)
        i += 1
    cmd += ['-Wall', '-Wextra', '-Werror', '-mgeneral-regs-only', '-I', str(ROOT / '.cache/xnu-work/osfmk'), '-I', str(ROOT / 'kits/uke'),
            '-I', str(ROOT / 'kits/Sm7675Platform/remoteproc'), '-I', str(ROOT / 'drivers/Ath12kWifi/include')]
    run_compile(cmd, rc['directory'], Path(dst).stem)

def object_symbols(data):
    commands = macho(data)['commands']
    st = next(p for c, p, _ in commands if c == 2)
    sy, count, strings, _ = struct.unpack_from('<4I', data, st + 8)
    found = []
    for i in range(count):
        ix, kind, _, _, va = struct.unpack_from('<IBBHQ', data, sy + i * 16)
        found.append((data[strings + ix:data.index(b'\0', strings + ix)].decode(), va, kind))
    return found

def object_vtable(data, found, name, count):
    """[(symbol, signed word)] of the first count entries of vtable name (authenticated pointer relocations)."""
    va = next(v for n, v, _ in found if n == name)
    entries = {}
    for segment in macho(data)['segments']:
        for sec in segment['sections']:
            if sec['va'] <= va < sec['va'] + sec['size']:
                ro, rc = struct.unpack_from('<2I', data, sec['command_offset'] + 56)
                for i in range(rc):
                    offset, bits = struct.unpack_from('<2I', data, ro + i * 8)
                    if bits >> 27 & 1 and bits >> 25 & 3 == 3 and bits >> 28 == 11:
                        entries[sec['va'] + offset] = (found[bits & 0xffffff][0],
                                                       struct.unpack_from('<Q', data, sec['fileoff'] + offset)[0])
    return [entries.get(va + 16 + i * 8) for i in range(count)]

# --- end of the copied helpers ---

XDCI_FIXTURE = Path(__file__).resolve().with_name('usb-xdci-24A446.json')
XHCI_FIXTURE = Path(__file__).resolve().with_name('usb-xhci-24A446.json')
DISPLAY_FIXTURE = Path(__file__).resolve().with_name('legacy-display-24A446.json')

usb_abi = json.loads(XDCI_FIXTURE.read_text())
xhci_abi = json.loads(XHCI_FIXTURE.read_text())
display_abi = json.loads(DISPLAY_FIXTURE.read_text())
slide = int(usb_abi['collection_slide'], 16)
# The abstract-slot target: slot 214 is an IOService leaf (legacy-display fixture, "leaves"),
# so every vtable entry that still points there is a slot the original leaves abstract.
ABSTRACT = int(display_abi['slots'][214][0], 16)
usb_slots = [(int(t, 16), int(d, 16)) for t, d in usb_abi['slots']]
usb_methods = {int(k): v for k, v in usb_abi['methods'].items()}
usb_pure = {i for i, (t, _) in enumerate(usb_slots) if t == ABSTRACT}
require((USB / 'QcomDwc3.cpp').exists(), 'QcomDwc3 module directory (select the QcomDwc3 component)')
USB_OUT = OUT
usb_names = ({int(k): v for k, v in usb_abi['macros'].items()} if 'macros' in usb_abi else
             {179: 'XDCI_KEEP_CONFIGURATIONS', 197: 'XDCI_CONNECTION_DONE', 227: 'XDCI_DESIRED_POWER',
              258: 'XDCI_UPDATE_REGISTER_FIELD',
              267: 'XDCI_TRANSPORT_ACTIVE', 268: 'XDCI_POWER_ON',
              269: 'XDCI_POWER_OFF', 270: 'XDCI_PARAMETERS', 275: 'XDCI_CONTROLLER_RESET', 277: 'XDCI_PERIODIC_USAGE',
              278: 'XDCI_PERIODIC_SET'})
usb_header = '''// Generated from the reviewed AppleUSBXDCI fixture; opaque private storage only.
#pragma once
#include <IOKit/IOService.h>
'''
for slot, macro in usb_names.items():
    usb_header += f'#define {macro} {usb_methods[slot]["name"]}\n'
usb_header += f'''class AppleUSBXDCI : public IOService {{
 OSDeclareAbstractStructors(AppleUSBXDCI)
 unsigned char originalPrivate[{usb_abi['instance_size']}-sizeof(IOService)];
public:
 void free() override;
 bool init(OSDictionary *properties=nullptr) override;
 IOReturn setProperties(OSObject *) override;
 bool start(IOService *) override;
 void stop(IOService *) override;
 bool terminate(IOOptionBits options=0) override;
 IOWorkLoop *getWorkLoop() const override;
 IOReturn callPlatformFunction(const OSSymbol *,bool,void *,void *,void *,void *) override;
 IOReturn setPowerState(unsigned long,IOService *) override;
 unsigned long maxCapabilityForDomainState(IOPMPowerFlags) override;
 unsigned long initialPowerStateForDomainState(IOPMPowerFlags) override;
 IOReturn powerStateWillChangeTo(IOPMPowerFlags,unsigned long,IOService *) override;
 IOReturn powerStateDidChangeTo(IOPMPowerFlags,unsigned long,IOService *) override;
'''
for i in range(usb_abi.get('base_slots', 168), len(usb_slots)):
    m = usb_methods[i]
    suffix = '=0' if i in usb_pure else ''
    usb_header += f' virtual {m["returns"]} {m["name"]}({m["parameters"]}){suffix};\n'
usb_header += f'}};\nstatic_assert(sizeof(AppleUSBXDCI)=={usb_abi["instance_size"]},"Original controller instance size");\n'
(USB_OUT / 'OriginalUsbXdciABI.h').write_text(usb_header)

xhci_slots = [(int(t, 16), int(d, 16)) for t, d in xhci_abi['slots']]
xhci_methods = {int(k): v for k, v in xhci_abi['methods'].items()}
xhci_names = ({int(k): v for k, v in xhci_abi['macros'].items()} if 'macros' in xhci_abi else
              {188: 'XHCI_DESIRED_POWER', 315: 'XHCI_READ64', 317: 'XHCI_WRITE64'})
xhci_header = '''// Generated from the reviewed AppleUSBXHCI fixture; opaque private storage only.
#pragma once
#include <IOKit/IOService.h>
'''
for slot, macro in xhci_names.items():
    xhci_header += f'#define {macro} {xhci_methods[slot]["name"]}\n'
xhci_header += '''class AppleUSBXHCI : public IOService {{
 OSDeclareAbstractStructors(AppleUSBXHCI)
 unsigned char originalPrivate[{instance_size}-sizeof(IOService)];
public:
 void free() override;
 bool init(OSDictionary *properties=nullptr) override;
 IOReturn setProperties(OSObject *) override;
 bool didTerminate(IOService *,IOOptionBits,bool *) override;
 void systemWillShutdown(IOOptionBits) override;
 void registerService(IOOptionBits options=0) override;
 bool start(IOService *) override;
 void stop(IOService *) override;
 bool open(IOService *,IOOptionBits options=0,void *arg=nullptr) override;
 void close(IOService *,IOOptionBits options=0) override;
 bool handleOpen(IOService *,IOOptionBits,void *) override;
 void handleClose(IOService *,IOOptionBits) override;
 bool handleIsOpen(const IOService *) const override;
 bool terminate(IOOptionBits options=0) override;
 bool matchPropertyTable(OSDictionary *,SInt32 *) override;
 bool attach(IOService *) override;
 IOWorkLoop *getWorkLoop() const override;
 IOReturn message(UInt32,IOService *,void *arg=nullptr) override;
 void PMstop() override;
 IOReturn setPowerState(unsigned long,IOService *) override;
 unsigned long maxCapabilityForDomainState(IOPMPowerFlags) override;
 unsigned long initialPowerStateForDomainState(IOPMPowerFlags) override;
 IOReturn powerStateWillChangeTo(IOPMPowerFlags,unsigned long,IOService *) override;
 IOReturn powerStateDidChangeTo(IOPMPowerFlags,unsigned long,IOService *) override;
'''.replace('{instance_size}', str(xhci_abi['instance_size']))
for i in range(xhci_abi.get('base_slots', 168), len(xhci_slots)):
    m = xhci_methods[i]
    xhci_header += f' virtual {m["returns"]} {m["name"]}({m["parameters"]});\n'
xhci_header += f'}};\nstatic_assert(sizeof(AppleUSBXHCI)=={xhci_abi["instance_size"]},"Original controller instance size");\n'
(USB_OUT / 'OriginalUsbXhciABI.h').write_text(xhci_header)
require(all(fixture['kernelcache_sha256'] == usb_abi['kernelcache_sha256'] and
            int(fixture['collection_slide'], 16) == slide for fixture in (xhci_abi, display_abi)),
        'USB ABI fixtures must match the display fixture fileset and slide')
print(f'USB ABI: XDCI {len(usb_slots)} slots ({len(usb_pure)} abstract), '
      f'XHCI {len(xhci_slots)} slots, slide {slide:#x}')
if CHECK_ONLY:
    print('check: fixtures and generated headers are consistent; not compiling')
    raise SystemExit(0)

sdk = run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()

# The three display symbols UkeUsbBoard.cpp calls from its DP-AUX route. With the display
# archive absent (this round) they must still resolve; the stubs fail closed and the DP path
# stays inert, which is what a USB-only kernel wants. Same pattern as package/UfsDpuStubs.c.
BOARD_STUB = USB_OUT / 'UsbBoardStubs.cpp'
BOARD_STUB.write_text("""// SPDX-License-Identifier: BSD-2-Clause-Patent
// Generated by scripts/build-uke-usb.py when the legacy display/backlight archive is not linked.
// UkeUsbBoard.cpp carries the DP-AUX/DPU route and serializes WCD939x access through the first
// backlight chip's GENI SE0, so a USB-only kernel still references four board entries that live in
// the display track. The answers below are the "that driver is not running" answers, not do-nothing
// success: no DP debug, no DPU scanout, and the real helper's own not-ready reply
// (package/AppleARMBacklight.cpp: uke_backlight_with_wcd_bus returns kIOReturnNotReady while no
// ShengBacklight has published).
#include "UkeUsbBoard.h"
#include <IOKit/IOReturn.h>
extern "C" bool native_legacy_dp_debug() { return false; }
extern "C" bool native_legacy_panel_dma_left_ready() { return false; }
extern "C" IOReturn native_legacy_panel_route_dma(bool) { return kIOReturnUnsupported; }
IOReturn uke_backlight_with_wcd_bus(UkeWcdBusAction, void *) { return kIOReturnNotReady; }
""")

usb_flags = ('-DQCOM_DWC3_UKE_USB2', '-I', str(USB_OUT), '-I', str(USB), '-I', str(KITS),
             '-I', str(remoteproc))
compile_cpp(USB / 'QcomDwc3.cpp', USB_OUT / 'QcomDwc3.o', usb_flags)
compile_cpp(KITS / 'UsbProbe.cpp', USB_OUT / 'UsbProbe.o', usb_flags)
compile_cpp(KITS / 'UkeUsbBoard.cpp', USB_OUT / 'UkeUsbBoard.o',
            (*usb_flags, '-I', str(ROOT / 'drivers/GopFramebuffer')))
board_objects = []
compile_c(KITS / 'GeniI2c.c', USB_OUT / 'GeniI2c.o')
board_objects.append(USB_OUT / 'GeniI2c.o')
compile_c(remoteproc / 'QcomRpmh.c', USB_OUT / 'QcomRpmh.o')
board_objects.append(USB_OUT / 'QcomRpmh.o')
if not DISPLAY_ARCHIVE.is_file():
    print('display archive absent: linking the inert DP/backlight fallbacks')
    compile_cpp(BOARD_STUB, USB_OUT / 'UsbBoardStubs.o')
    board_objects.append(USB_OUT / 'UsbBoardStubs.o')

usb_raw = (USB_OUT / 'QcomDwc3.o').read_bytes()
usb_found = object_symbols(usb_raw)
usb_bindings = {n: int(v, 16) + slide for n, v in usb_abi['bindings'].items()}
usb_vt = '__ZTV12QcomDwc3Xdci'
usb_entries = object_vtable(usb_raw, usb_found, usb_vt, len(usb_slots))
require(all(usb_entries), f'{usb_vt}: at least the {len(usb_slots)} AppleUSBXDCI entries')
for i, (name, word) in enumerate(usb_entries):
    require(word >> 63 and word >> 48 & 1 and word >> 49 & 3 == 0 and (word >> 32) & 0xffff == usb_slots[i][1],
            f'{usb_vt} slot {i} ({name}) signed with the original diversity {usb_slots[i][1]:#x}')
    if '12AppleUSBXDCI' in name:
        require(i not in usb_pure, f'{usb_vt} slot {i} ({name}) implements an abstract original slot')
        require(usb_bindings.setdefault(name, usb_slots[i][0] + slide) == usb_slots[i][0] + slide, f'{name}: one original address')
    elif not name.startswith(('__ZN12QcomDwc3Xdci', '__ZNK12QcomDwc3Xdci')):
        require(i < usb_abi.get('base_slots', 168), f'{usb_vt} slot {i} ({name}) is inherited from IOService')
usb_slot_by_diversity = {d: i for i, (_, d) in enumerate(usb_slots)}
for name in sorted({n for n, _, k in usb_found if k == 1 and '12AppleUSBXDCI' in n} - set(usb_bindings)):
    slot = usb_slot_by_diversity.get(discriminator(name[1:]))
    require(slot is not None and slot not in usb_pure, f'{name}: an original concrete AppleUSBXDCI slot')
    usb_bindings[name] = usb_slots[slot][0] + slide

xhci_bindings = {n: int(v, 16) + slide for n, v in xhci_abi['bindings'].items()}
xhci_vt = '__ZTV12QcomDwc3Xhci'
xhci_entries = object_vtable(usb_raw, usb_found, xhci_vt, len(xhci_slots))
require(all(xhci_entries), f'{xhci_vt}: at least the {len(xhci_slots)} AppleUSBXHCI entries')
for i, (name, word) in enumerate(xhci_entries):
    require(word >> 63 and word >> 48 & 1 and word >> 49 & 3 == 0 and (word >> 32) & 0xffff == xhci_slots[i][1],
            f'{xhci_vt} slot {i} ({name}) signed with the original diversity {xhci_slots[i][1]:#x}')
    if '12AppleUSBXHCI' in name:
        require(xhci_bindings.setdefault(name, xhci_slots[i][0] + slide) == xhci_slots[i][0] + slide, f'{name}: one original address')
    elif not name.startswith(('__ZN12QcomDwc3Xhci', '__ZNK12QcomDwc3Xhci')):
        require(i < xhci_abi.get('base_slots', 168), f'{xhci_vt} slot {i} ({name}) is inherited from IOService')
XHCI_BASE = xhci_abi.get('base_slots', 168)
kernel_text = range(*(int(x, 16) for x in xhci_abi['kernel_text'])) if 'kernel_text' in xhci_abi else range(0xfffffe000b000000, 1 << 64)
xhci_io = {i for i, (t, _) in enumerate(xhci_slots[:XHCI_BASE]) if t not in kernel_text and i not in (0, 1, 7)}
for i in sorted(xhci_io):
    require('12AppleUSBXHCI' in xhci_entries[i][0] or 'QcomDwc3Xhci' in xhci_entries[i][0], f'{xhci_vt} slot {i} ({xhci_entries[i][0]}) reaches the original override')
xhci_slot_by_diversity = {d: i for i, (_, d) in enumerate(xhci_slots)}
for name in sorted({n for n, _, k in usb_found if k == 1 and '12AppleUSBXHCI' in n} - set(xhci_bindings)):
    slot = xhci_slot_by_diversity.get(discriminator(name[1:]))
    require(slot is not None, f'{name}: an original concrete AppleUSBXHCI slot')
    xhci_bindings[name] = xhci_slots[slot][0] + slide
usb_bindings.update(xhci_bindings)
print(f'vtable: XDCI {len(usb_entries)} entries and XHCI {len(xhci_entries)} entries all carry the original diversity')

USB_DATA = {'__ZN12AppleUSBXDCI10gMetaClassE', '__ZTV12AppleUSBXDCI', '__ZN12AppleUSBXHCI10gMetaClassE', '__ZTV12AppleUSBXHCI'}
usb_text, usb_addresses = '.text\n.p2align 2\n', ''
for name, va in sorted(usb_bindings.items()):
    if name in USB_DATA:
        usb_addresses += f'.globl {name}\n.set {name}, {va:#x}\n'
    else:
        usb_addresses += f'.globl {name}.original\n.set {name}.original, {va:#x}\n'
        usb_text += f'.globl {name}\n{name}:\n bti c\n b {name}.original\n'
(USB_OUT / 'usb-bindings.S').write_text(usb_text)
(USB_OUT / 'usb-addresses.S').write_text(usb_addresses)
for stem in ('usb-bindings', 'usb-addresses'):
    run(['xcrun', 'clang', '-target', TRIPLE, '-isysroot', sdk, '-c', USB_OUT / (stem + '.S'), '-o', USB_OUT / (stem + '.o')])
usb_late = USB_OUT / 'QcomDwc3.late.o'
run([llvm / 'llvm-objcopy', '--remove-section=__DATA,__mod_init_func', '--globalize-symbol=__GLOBAL__sub_I_QcomDwc3.cpp',
     USB_OUT / 'QcomDwc3.o', usb_late])

objects = [usb_late, USB_OUT / 'usb-bindings.o', USB_OUT / 'usb-addresses.o', USB_OUT / 'UsbProbe.o', USB_OUT / 'UkeUsbBoard.o']
objects += board_objects
archive = OUT / 'libUkeUsb.a'
archive.unlink(missing_ok=True)
run(['xcrun', 'ar', 'rcs', archive, *objects])

report = {
    'archive': str(archive.relative_to(ROOT)),
    'archive_sha256': sha(archive),
    'archive_bytes': archive.stat().st_size,
    'soc': 'SM7675 (cliffs), device-mode USB2',
    'define': 'QCOM_DWC3_UKE_USB2',
    'fixtures': {p.name: sha(p) for p in (XDCI_FIXTURE, XHCI_FIXTURE, DISPLAY_FIXTURE)},
    'vtable': {'xdci_slots': len(usb_slots), 'xdci_abstract': len(usb_pure),
               'xhci_slots': len(xhci_slots), 'slide': hex(slide),
               'xdci_entries': len(usb_entries), 'xhci_entries': len(xhci_entries)},
    'sources': {str(p.relative_to(ROOT)): sha(p) for p in
                (USB / 'QcomDwc3.cpp', KITS / 'UsbProbe.cpp', KITS / 'UkeUsbBoard.cpp',
                 KITS / 'GeniI2c.c', remoteproc / 'QcomRpmh.c')},
    'objects': [{'object': Path(o).name, 'bytes': Path(o).stat().st_size} for o in objects],
    'display_archive_linked': DISPLAY_ARCHIVE.is_file(),
}
(OUT / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
print(f'archive: {archive.relative_to(ROOT)} ({report["archive_bytes"]:,} bytes, {len(objects)} objects)')
print(f'report : {(OUT / "build-report.json").relative_to(ROOT)}')
