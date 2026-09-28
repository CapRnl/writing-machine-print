"""设置：第一次运行从奎享雕刻的配置里取默认值，之后保存在本工具目录的 设置.json。"""

import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(APP_DIR, '设置.json')
QUEUE_PATH = os.path.join(APP_DIR, '队列.json')     # 待写队列和每份打印到第几页，关掉程序再开还在
HANDCOPY_NAME = '写字机手抄队列'


def find_handcopy_dir():
    """"手抄队列"文件夹：每月要用写字机手抄的定稿放在这里，按月分文件夹（如"9月份会议"）。
    在 OneDrive、文档、桌面下面找名为"写字机手抄队列"的文件夹（本身或下一层）；设置.json 里的 handcopy_dir 优先。"""
    roots = [os.environ.get(k) for k in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial')]
    home = os.path.expanduser('~')
    roots += [os.path.join(home, n) for n in ('OneDrive', 'Documents', 'Desktop')]
    seen = set()
    for r in roots:
        if not r or r in seen or not os.path.isdir(r):
            continue
        seen.add(r)
        p = os.path.join(r, HANDCOPY_NAME)
        if os.path.isdir(p):
            return p
        try:
            for n in sorted(os.listdir(r)):
                p = os.path.join(r, n, HANDCOPY_NAME)
                if os.path.isdir(p):
                    return p
        except OSError:
            pass
    return ''


HANDCOPY_DIR = find_handcopy_dir()
FONT_DIR = os.path.join(APP_DIR, 'fonts')
KJ_INSTALL = r'C:\Program Files\奎享雕刻'
KJ_FONTS = os.path.join(KJ_INSTALL, 'gcodeFonts')
KJ_DATA = os.path.join(os.path.expanduser('~'), '.KenjoyDraw')

# 笔迹：同一个人写的几个版本放在一起轮换（2026-09-27 从本机 703 个字库里挑的，见 fonts 目录）
#   陈继世：用户一直在用的字体，同一人写的 5 个版本；7 和完整版的句号是实心点，标点只从 1/2/6 里取
#   真迹：blood8 真迹系列，端正自然的另一种手写，5 个版本（3/4/5 只收常用 3500 字），给不同记录人用
#   （"陈继世2 单版"是陈继世多版轮换的次优版，09-27 按用户"只留最优"删除）
#   （"陈继世-老宫"09-27 真机试写后删除：字是碎笔画拼的，平均每字落笔 11.9 次、是其他版本的 4 倍，
#     写起来"点点点"。选字库要看落笔次数和短于 0.6 毫米的碎笔画，不能只看样子）
HANDS = {
    '陈继世': {'label': '陈继世（5 个版本轮换）',
               'files': ['陈继世-1.gfont', '陈继世-2.gfont', '陈继世-6.gfont', '陈继世-7.gfont',
                         '陈继世-完整版.gfont'],
               'punct': ['陈继世-1.gfont', '陈继世-2.gfont', '陈继世-6.gfont']},
    '真迹': {'label': '真迹（5 个版本轮换）',
             'files': ['真迹-1.gfont', '真迹-2.gfont', '真迹-3.gfont', '真迹-4.gfont', '真迹-5.gfont'],
             'punct': None},
}
OBSOLETE_HANDS = {'陈继世2': '陈继世'}
# 上面的文件名是本程序 fonts 目录里的叫法；从奎享雕刻"在线字库"下载的原名是"字库名_授权_作者.gfont"
# （如"陈继世1_kvenjoy_1.gfont"），放进 fonts 目录或留在奎享的 gcodeFonts 目录里都能认，不用改名。
# 注意"陈继世_kvenjoy_老宫.gfont"（作者老宫）不在其中：它的字是碎笔画拼的，见上。
FONT_ALIASES = {
    '陈继世-1.gfont': '陈继世1_', '陈继世-2.gfont': '陈继世2_', '陈继世-6.gfont': '陈继世6_',
    '陈继世-7.gfont': '陈继世7_', '陈继世-完整版.gfont': '陈继世完整版_',
    '真迹-1.gfont': 'blood8真迹_', '真迹-2.gfont': 'blood8真迹2_', '真迹-3.gfont': 'blood8真迹3_',
    '真迹-4.gfont': 'blood8真迹4_', '真迹-5.gfont': 'blood8真迹5_',
}

# 字在一行里的高度：字形基线放在行高的这个比例处（从上一条横线往下量，1 = 压在下一条横线上）。
# 09-27 真机试写发现字整体偏低、横线从字的下部穿过；量奎享"笔记"预览，墨迹中位高度在下横线上方 4.71 毫米，
# 本程序原来（1.0）是 2.56 毫米，差 2.15 毫米 = 行高（10.24）的 0.21，所以取 0.79，与奎享一致（字在行内大致居中）。
BASE_LINE = 0.79


def _read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _kj_machine_dir():
    machines = _read_json(os.path.join(KJ_DATA, 'MachineData[].json'), []) or []
    name = None
    for m in machines:
        if m.get('isDefault'):
            name = m.get('name')
    if not name and machines:
        name = machines[0].get('name')
    return os.path.join(KJ_DATA, name or '默认Grbl机器')


def defaults_from_kenjoy():
    d = _kj_machine_dir()
    grbl = _read_json(os.path.join(d, 'Grbl.json'), {}) or {}
    st = _read_json(os.path.join(d, 'settings.json'), {}) or {}
    axes = _read_json(os.path.join(d, 'AxesData.json'), {}) or {}
    ctl = _read_json(os.path.join(d, 'MachineControllerData.json'), {}) or {}
    fset = _read_json(os.path.join(d, 'FontsSetData.json'), {}) or {}
    pages = {p.get('name'): p for p in (_read_json(os.path.join(d, 'page_settings.json'), []) or [])}

    def pct_of(page_name, fallback=95.0):
        p = pages.get(page_name) or {}
        return float(p.get('fontSize') or fallback)

    def gap_of(page_name, fallback=1.0):
        p = pages.get(page_name) or {}
        return float(p.get('space') if p.get('space') is not None else fallback)

    return {
        'machine': {
            'port': '',
            'baud': int(ctl.get('baud') or 115200),
            'feed': float(st.get('feedRate') or 4000),
            'travel': float(st.get('jogSpeed') or 8000),
            'pen_type': 'Stepper' if (grbl.get('type') or 'Stepper') == 'Stepper' else 'Servo',
            'z_down': float(grbl.get('zOn') if grbl.get('zOn') is not None else 6.0),
            'z_up': float(grbl.get('zOff') if grbl.get('zOff') is not None else 0.0),
            'z_speed': float(grbl.get('zSpeed') or 10000),
            # 两笔之间只抬一点的高度；None = 抬满。09-27 试过 2.5：整页拖线（笔根本没离纸），用户量的"笔尖离纸约 5 毫米"
            # 和 Z 数值对不上。要再用，先做落笔点标定（在废纸上按不同高度各划一道，看从哪个高度开始有墨），不能凭估计
            'z_hover': None,
            # 写字机里的加速度 $120/$121、拐角参数 $11：连接时按这里调（09-27 用户同意试的提速），原值记在 tune_original
            'tune': {'120': 5000, '121': 5000, '11': 0.02},
            'tune_original': {},
            'servo_down': int(grbl.get('laserS') or 1000),
            'pen_down_delay': float(st.get('toolOnDelay') or 0.0),
            'pen_up_delay': float(st.get('toolOffDelay') or 0.0),
            'near': float(st.get('nearDst') or 0.3),
            'flip_x': bool(axes.get('xAxeRev', False)),
            'flip_y': bool(axes.get('yAxeRev', False)),
            'swap_xy': bool(axes.get('revxy', False)),
            'offset_x': float(st.get('offsetX') or 0.0),
            'offset_y': float(st.get('offsetY') or 0.0),
            'jog_step': 1.0,
        },
        'style': {
            'amount': 1.0,                # 手写起伏的幅度倍数
            'base_line': BASE_LINE,
            'indent_half_spaces': 2,
        },
        'hand_default': {'meeting': '陈继世', 'study': '陈继世'},
        'recorder_hands': {},             # 记录人 → 笔迹（用户给某份记录换过笔迹后记住）
        'forms': {
            'meeting': {'font_pct': pct_of('党建会议记录第一页'), 'gap': gap_of('党建会议记录第一页'),
                        'offset_x': 0.0, 'offset_y': 0.0, 'header_shift': 0.0},
            'study': {'font_pct': pct_of('政治理论学习'), 'gap': gap_of('政治理论学习'),
                      'offset_x': 0.0, 'offset_y': 0.0, 'header_shift': 0.0},
        },
        # 政治理论学习记录的表头（原稿里没有）：第一次为空，之后按上次打印时填的值带出；学习时间不带出（见 app._parse_file）
        'study_fields': {'time': '', 'place': '', 'host': '', 'recorder': '', 'expected': '', 'actual': '', 'absent': ''},
        'handcopy_dir': '',               # 手抄队列文件夹；空 = 自动找（见 find_handcopy_dir）
        'last_dir': '',
    }


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load():
    base = defaults_from_kenjoy()
    saved = _read_json(SETTINGS_PATH, None)
    if saved:
        _merge(base, saved)
    # 清掉已删除的旧选项（奎享式独立抖动、陈继世2 单版）
    for k in ('mode', 'rand_size', 'rand_rotate', 'rand_y'):
        base['style'].pop(k, None)
    base['machine'].pop('paper_rotation', None)   # 09-27 误加的"纸的摆放"，已删
    if base['style'].get('base_line') == 1.0:     # 旧默认"字底压在横线上"，比奎享低 2.15 毫米
        base['style']['base_line'] = BASE_LINE
    mig = base.setdefault('migrations', [])
    if 'speed-0927' not in mig:
        # 09-27 用户同意的提速：写字 4000→5000、空走 8000→12000（写字机最高速度），配合 tune 里的加速度 5000、拐角 0.02。
        # 只改一次；之后用户或 Claude 改回别的值不会再被改掉
        mc = base['machine']
        if mc.get('feed') == 4000:
            mc['feed'] = 5000.0
        if mc.get('travel') == 8000:
            mc['travel'] = 12000.0
        mig.append('speed-0927')
    if 'hover-off-0927' not in mig:
        # 09-27 22:29 试写：两笔之间抬到 2.5 时笔没离纸、整页拖线，改回抬满（设置里存过的 2.5 一并清掉）
        base['machine']['z_hover'] = None
        mig.append('hover-off-0927')
    for d in (base.get('hand_default', {}), base.get('recorder_hands', {})):
        for k, v in list(d.items()):
            if v not in HANDS:
                d[k] = OBSOLETE_HANDS.get(v, '陈继世')
    return base


def save(cfg):
    tmp = SETTINGS_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_PATH)


def load_queue():
    return _read_json(QUEUE_PATH, None)


def save_queue(data):
    tmp = QUEUE_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, QUEUE_PATH)


def missing_fonts(hand):
    """这种笔迹缺哪几个字库文件（本程序 fonts 目录和奎享字库目录里都没找到的）。"""
    return [f for f in (HANDS.get(hand) or {}).get('files', []) if not os.path.exists(font_path(f))]


def font_path(name):
    """字库文件的位置：先找本程序 fonts 目录，再找奎享雕刻的字库目录；都按本名和奎享下载时的原名找。"""
    if os.path.isabs(name):
        return name
    prefix = FONT_ALIASES.get(name)
    for d in (FONT_DIR, KJ_FONTS):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
        if prefix and os.path.isdir(d):
            hits = sorted(n for n in os.listdir(d) if n.startswith(prefix) and n.lower().endswith('.gfont'))
            if hits:
                return os.path.join(d, hits[0])
    return os.path.join(FONT_DIR, name)
