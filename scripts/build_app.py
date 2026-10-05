"""Build a self-contained, double-clickable application for macOS or Windows.

PyInstaller freezes the interpreter, the pinned GUI dependencies and everything under
``src`` into one bundle, so the result opens with a double-click on a machine that has
neither Python nor ``pip install``. Bundles are not cross-compiled: build the ``.app`` on
macOS and the ``.exe`` on Windows, then ship the archive this script produces.
"""

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
for SEARCH in (ROOT / 'scripts', ROOT / 'src'):
    if str(SEARCH) not in sys.path:
        sys.path.insert(0, str(SEARCH))

from make_macos_app import APP_NAME, BUNDLE_IDENTIFIER, build_icon, sign  # noqa: E402
from platform_support import APP_DISPLAY_NAME, APP_VERSION  # noqa: E402

ENTRY_SCRIPT = ROOT / 'src' / 'app.py'
ASSET_DIRECTORY = ROOT / 'src' / 'assets'
LOGO_FILE = ASSET_DIRECTORY / 'app_logo.png'
PYINSTALLER_REQUIREMENT = 'pyinstaller>=6.0'
WINDOWS_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
SMOKE_TEST_TIMEOUT = 90  # 首次冷启动要过 Gatekeeper 检查并加载 Qt，留足时间；崩溃会在此之前退出。
INNO_SETUP_CANDIDATES = (r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
                         r'C:\Program Files\Inno Setup 6\ISCC.exe')

INNO_TEMPLATE = """[Setup]
AppName={name}
AppVersion={version}
DefaultDirName={{autopf}}\\{directory}
DefaultGroupName={name}
DisableProgramGroupPage=yes
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
UninstallDisplayIcon={{app}}\\{executable}
OutputDir={output_dir}
OutputBaseFilename={base_filename}

[Languages]
Name: "default"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{{cm:CreateDesktopIcon}}"; GroupDescription: "{{cm:AdditionalIcons}}"

[Files]
Source: "{source}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{autoprograms}}\\{name}"; Filename: "{{app}}\\{executable}"
Name: "{{autodesktop}}\\{name}"; Filename: "{{app}}\\{executable}"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\{executable}"; Description: "{{cm:LaunchProgram,{name}}}"; Flags: nowait postinstall skipifsilent
"""


def execute(command, note, env=None):
    """Run one build step, echoing it, and return the process exit code."""
    print('==> ' + note)
    print('    ' + ' '.join('"{0}"'.format(part) if ' ' in str(part) else str(part)
                            for part in command))
    return subprocess.run([str(part) for part in command], cwd=str(ROOT), env=env,
                          check=False).returncode


def ensure_pyinstaller(allow_install):
    """Guarantee that the current interpreter can run PyInstaller."""
    probe = subprocess.run([sys.executable, '-c', 'import PyInstaller'], cwd=str(ROOT),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if probe.returncode == 0:
        return True
    if not allow_install:
        print('缺少 PyInstaller，请重跑并加上 --install，或手动执行：{} -m pip install "{}"'.format(
            sys.executable, PYINSTALLER_REQUIREMENT))
        return False
    return execute([sys.executable, '-m', 'pip', 'install', PYINSTALLER_REQUIREMENT],
                   '安装打包工具 PyInstaller') == 0


def build_macos_icon(work):
    """Render the checkout logo into AppIcon.icns, or None when the toolchain is missing."""
    resources = work / 'icon'
    resources.mkdir(parents=True, exist_ok=True)
    return resources / 'AppIcon.icns' if LOGO_FILE.is_file() and build_icon(LOGO_FILE, resources) else None


def icon_png(image, size, aspect_mode, transform_mode):
    """One PNG payload for the Windows .ico container."""
    from PySide6.QtCore import QBuffer, QIODevice
    scaled = image.scaled(size, size, aspect_mode, transform_mode)
    buffer = QBuffer()
    if not buffer.open(QIODevice.WriteOnly) or not scaled.save(buffer, 'PNG'):
        return b''
    return bytes(buffer.data())


def ico_container(images):
    """ICO layout: one directory entry per image, then every PNG payload in order."""
    directory = [struct.pack('<HHH', 0, 1, len(images))]
    payloads = []
    offset = 6 + 16 * len(images)
    for size, payload in images:
        # Width and height are stored modulo 256, so a zero byte means 256 pixels.
        directory.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32,
                                     len(payload), offset))
        payloads.append(payload)
        offset += len(payload)
    return b''.join(directory + payloads)


def build_windows_icon(work, name):
    """Write a multi-size .ico holding PNG payloads; Qt scales the logo, so no Pillow."""
    if not LOGO_FILE.is_file():
        return None
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    source = QImage(str(LOGO_FILE))
    if source.isNull():
        return None
    images = [(size, payload) for size, payload in
              ((size, icon_png(source, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
               for size in WINDOWS_ICON_SIZES) if payload]
    if not images:
        return None
    icon = work / (name + '.ico')
    icon.write_bytes(ico_container(images))
    return icon


def pyinstaller_command(args, work, icon):
    """Assemble the PyInstaller call for a windowed one-directory bundle.

    Nothing is force-collected: the build host is the target platform, so static analysis
    of ``src/app.py`` follows exactly the Qt bindings and the platform branch the
    application really uses. ``--collect-submodules qfluentwidgets`` would instead drag in
    QtWebEngine and QtQuick3D (300 MB) that the interface never touches.
    """
    command = [sys.executable, '-m', 'PyInstaller',
               '--noconfirm', '--clean', '--windowed',
               '--name', args.name,
               '--distpath', str(args.output),
               '--workpath', str(work / 'pyinstaller'),
               '--specpath', str(work),
               '--paths', str(ROOT / 'src'),
               '--add-data', str(ASSET_DIRECTORY) + os.pathsep + 'assets']
    if icon is not None:
        command += ['--icon', str(icon)]
    return command + [str(ENTRY_SCRIPT)]


def numeric_version(label):
    """包内元数据只接受数字：去掉发布标签的 v 前缀（v3 → 3，9.9 原样保留）。"""
    return label[1:] if label[:1].lower() == 'v' else label


def patch_macos_plist(app, name, version):
    """Merge the identity that Finder and the Dock read into the freshly built bundle."""
    plist = app / 'Contents' / 'Info.plist'
    payload = plistlib.loads(plist.read_bytes())
    number = numeric_version(version)
    payload.update({
        'CFBundleName': name,
        'CFBundleDisplayName': APP_DISPLAY_NAME,
        'CFBundleIdentifier': BUNDLE_IDENTIFIER,
        'CFBundleShortVersionString': number,
        'CFBundleVersion': number,
        'LSMinimumSystemVersion': '11.0',
        'LSApplicationCategoryType': 'public.app-category.utilities',
        'NSHighResolutionCapable': True,
    })
    if (app / 'Contents' / 'Resources' / 'AppIcon.icns').is_file():
        payload['CFBundleIconFile'] = 'AppIcon'
    plist.write_bytes(plistlib.dumps(payload))


def archive(source, target):
    """Zip a bundle; ditto keeps the macOS executable bits and the code signature."""
    ditto = shutil.which('ditto')
    if sys.platform == 'darwin' and ditto:
        return execute([ditto, '-c', '-k', '--keepParent', str(source), str(target)],
                       '压缩 ' + Path(target).name) == 0
    if Path(target).exists():
        os.remove(str(target))
    shutil.make_archive(str(Path(target).with_suffix('')), 'zip',
                        root_dir=str(Path(source).parent), base_dir=Path(source).name)
    return Path(target).is_file()


def build_macos_dmg(app, target):
    """Stage the bundle next to an Applications alias and freeze it into a dmg."""
    hdiutil = shutil.which('hdiutil')
    if hdiutil is None:
        print('未找到 hdiutil，跳过 dmg。')
        return False
    staging = target.parent / (target.stem + '-staging')
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    shutil.copytree(app, staging / app.name, symlinks=True)
    os.symlink('/Applications', str(staging / 'Applications'))
    done = execute([hdiutil, 'create', '-volname', app.stem, '-srcfolder', str(staging),
                    '-ov', '-format', 'UDZO', str(target)], '生成拖拽安装盘 dmg')
    shutil.rmtree(staging, ignore_errors=True)
    return done == 0


def find_inno_setup():
    for candidate in (shutil.which('iscc'), shutil.which('ISCC.exe'), *INNO_SETUP_CANDIDATES):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def windows_installer(bundle, args, base_filename):
    """Compile an Inno Setup wizard when ISCC is available on this machine."""
    iscc = find_inno_setup()
    if iscc is None:
        print('未找到 Inno Setup（iscc），跳过安装向导；zip 包同样免安装、可直接双击运行。')
        return False
    script = args.output / (args.name + '.iss')
    script.write_text(INNO_TEMPLATE.format(
        name=args.name, directory=args.name.replace(' ', ''), version=numeric_version(args.version),
        executable=args.name + '.exe', source=str(bundle), output_dir=str(args.output),
        base_filename=base_filename), encoding='utf-8-sig')
    return execute([str(iscc), '/Qp', str(script)], '生成 Windows 安装向导') == 0


def remove_collected_windows_icu(bundle):
    """Let Qt use Windows' ICU instead of a foreign DLL found on the build host."""
    internal = Path(bundle) / '_internal'
    candidates = [internal / 'icuuc.dll', *sorted(internal.glob('icudt*.dll'))]
    removed = []
    for path in candidates:
        if path.is_file():
            path.unlink()
            removed.append(path)
    return removed


def smoke_test(program, name):
    """Start the built program with an isolated data directory until it logs its startup line.

    The startup log is the proof of life: a missing Qt binding exits before it, while an
    unreachable SSH host only fails later and still keeps the interface running.
    """
    print('==> 启动验证 {}（最多 {} 秒）'.format(name, SMOKE_TEST_TIMEOUT))
    with tempfile.TemporaryDirectory(prefix='tunnel-smoke-') as folder:
        process = subprocess.Popen([str(program), '--data-dir', folder],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log = Path(folder) / 'logs' / 'ssh_tunnel.log'
        deadline = time.monotonic() + SMOKE_TEST_TIMEOUT
        started, exit_code = False, None
        while time.monotonic() < deadline:
            if log.is_file() and '已启动' in log.read_text(encoding='utf-8', errors='replace'):
                started = True
                break
            exit_code = process.poll()
            if exit_code is not None:
                break
            time.sleep(0.5)
        if exit_code is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
    if started:
        print('==> 产物启动验证通过（{} 已写入启动日志）。'.format(name))
    else:
        print('!! 产物启动验证失败：{}。'.format(
            '退出码 ' + str(exit_code) if exit_code is not None else '启动日志中没有启动记录'))
    return started


def directory_bytes(path):
    """Total size without double counting: framework folders repeat inode-shared files."""
    seen, total = set(), 0
    for folder, _, names in os.walk(path):
        for name in names:
            info = os.lstat(os.path.join(folder, name))
            if info.st_ino in seen:
                continue
            seen.add(info.st_ino)
            total += info.st_size
    return total


def describe(path):
    size = directory_bytes(path) if path.is_dir() else path.stat().st_size
    label = '目录' if path.is_dir() else '文件'
    return '{} （{}，约 {:.1f} MB）'.format(path.name, label, size / 1048576)


def parse_arguments(argv):
    parser = argparse.ArgumentParser(description='生成 macOS/Windows 自包含桌面应用（双击即可运行）')
    parser.add_argument('--output', default=str(ROOT / 'release'), help='产物目录，默认 release/')
    parser.add_argument('--name', default=APP_NAME, help='应用名，同时作为可执行文件名')
    parser.add_argument('--version', default=APP_VERSION,
                        help='版本标签，用于产物文件名；默认取 src/platform_support.py 的发布版本')
    parser.add_argument('--no-icon', action='store_true', help='跳过应用图标生成')
    parser.add_argument('--no-archive', action='store_true', help='只生成 bundle，不压缩')
    parser.add_argument('--dmg', action='store_true', help='macOS 额外生成拖拽安装 dmg')
    parser.add_argument('--installer', action='store_true',
                        help='Windows 额外用 Inno Setup 生成 setup.exe（构建机需已装 Inno Setup）')
    parser.add_argument('--smoke-test', action='store_true', help='构建后启动产物，验证能拉起界面')
    parser.add_argument('--install', action='store_true', help='缺少 PyInstaller 时自动 pip 安装')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_arguments(argv)
    if sys.platform not in ('darwin', 'win32'):
        print('PyInstaller 不能交叉打包：请在 macOS 上生成 .app，在 Windows 上生成 .exe。')
        return 1
    args.output = Path(args.output).expanduser()
    work = args.output / '.build'
    args.output.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    if not ensure_pyinstaller(args.install):
        return 1
    icon = None
    if not args.no_icon:
        icon = build_macos_icon(work) if sys.platform == 'darwin' else build_windows_icon(work, args.name)
        print('应用图标：' + ('未生成，使用默认图标。' if icon is None else str(icon)))

    # PyInstaller 的二进制缓存默认写在用户目录，这里放到产物目录内，便于离线与受限环境复用。
    environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(args.output / '.pyinstaller-cache'))
    if execute(pyinstaller_command(args, work, icon), '冻结解释器与依赖并生成 bundle', environment):
        return 1

    bundle = args.output / (args.name + '.app' if sys.platform == 'darwin' else args.name)
    program = (bundle / 'Contents' / 'MacOS' / args.name if sys.platform == 'darwin'
               else bundle / (args.name + '.exe'))
    if not program.is_file():
        print('未找到生成的 bundle：' + str(bundle))
        return 1
    if sys.platform == 'darwin':
        shutil.rmtree(args.output / args.name, ignore_errors=True)  # COLLECT 中间目录，.app 已包含全部内容
        patch_macos_plist(bundle, args.name, args.version)
        if not sign(bundle):
            print('提示：未完成 ad-hoc 签名，首次双击可能被 Gatekeeper 拦截。')
    else:
        removed_icu = remove_collected_windows_icu(bundle)
        if removed_icu:
            print('移除与 Windows 系统 ICU 冲突的构建机 DLL：' +
                  ', '.join(path.name for path in removed_icu))

    if args.smoke_test and not smoke_test(program, bundle.name):
        return 1

    stem = '{}-{}-{}'.format(args.name.replace(' ', '-'), args.version,
                             'macos' if sys.platform == 'darwin' else 'windows')
    artifacts = [bundle]
    if not args.no_archive:
        if archive(bundle, args.output / (stem + '.zip')):
            artifacts.append(args.output / (stem + '.zip'))
        if args.dmg and sys.platform == 'darwin' and build_macos_dmg(bundle, args.output / (stem + '.dmg')):
            artifacts.append(args.output / (stem + '.dmg'))
    if args.installer and sys.platform == 'win32':
        installer = args.output / (stem + '-installer.exe')
        if windows_installer(bundle, args, stem + '-installer') and installer.is_file():
            artifacts.append(installer)

    print('产物目录：' + str(args.output))
    for artifact in artifacts:
        print('  ' + describe(artifact))
    if sys.platform == 'darwin':
        print('把 .app 拖进“应用程序”后双击即可运行；数据存放在 ~/Library/Application Support/SshTunnelManager。')
        print('未做 Developer ID 签名与公证，分发到其他 Mac 时首次打开需右键 →“打开”。')
    else:
        print('解压后双击 "{}.exe" 即可运行；数据存放在 %LOCALAPPDATA%\\SshTunnelManager。'.format(args.name))
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
