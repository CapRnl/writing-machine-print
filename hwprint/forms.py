"""两种记录本的版式：首页表头栏目位置 + 正文页横线。

坐标单位毫米，原点 = 页面（表格/横线区）左上角，也就是写字前把笔尖对准的位置。
行距取自奎享雕刻"笔记"页面设置（会议记录 215/21，政治理论学习 204/20）。
栏目名位置是在两张首页底图（会议记录第一页.jpg、政治理论学习.jpg）上按像素量的：
  会议记录：左栏栏目名止于约 20.1，右栏"地点/实到人数/记录人"占 78.9~96.7；
  政治理论学习：左栏止于约 20.2，右栏占 70.9~90.0，"缺席人及原因"止于 24.0。
每项内容从栏目名末尾后 GAP 毫米起写。
"""

from dataclasses import dataclass, field

GAP = 3.0


@dataclass
class Cell:
    key: str          # 字段名
    label: str        # 表头上印好的栏目名（预览时画出来）
    row: int          # 第几行（0 起）
    x0: float         # 写字起点（毫米）
    x1: float         # 本格右边界（毫米）
    label_x: float = 0.0
    wrap_rows: list = field(default_factory=list)   # 写不下时续到哪些行：[(row, x0, x1), ...]


@dataclass
class BookForm:
    key: str
    name: str
    page_w: float
    first_rows: int          # 首页总行数（含表头行）
    first_pitch: float       # 首页行距
    body_start_row: int      # 首页正文从第几行开始（0 起）
    body_w: float            # 正文页宽
    body_h: float
    body_lines: int          # 正文页行数
    cells: list
    topic_row: int = -1      # "会议议题："所在行（-1 表示没有）
    topic_x0: float = 0.0
    extra_labels: list = field(default_factory=list)  # 只画在预览里的栏目名 [(row, x起, x止, text)]

    @property
    def body_pitch(self):
        return self.body_h / self.body_lines


MEETING = BookForm(
    key='meeting', name='会议记录本',
    page_w=149.0, first_rows=21, first_pitch=215.0 / 21, body_start_row=5,
    body_w=149.0, body_h=215.0, body_lines=21,
    cells=[
        Cell('name', '会议名称：', 0, 20.1 + GAP, 148.0, 2.8),
        Cell('time', '时    间：', 1, 20.0 + GAP, 76.5, 2.2),
        Cell('place', '地    点：', 1, 96.7 + GAP, 148.0, 78.8),
        Cell('expected', '应到人数：', 2, 19.9 + GAP, 76.5, 2.1),
        Cell('actual', '实到人数：', 2, 96.7 + GAP, 148.0, 78.9),
        Cell('host', '主 持 人：', 3, 19.9 + GAP, 76.5, 2.1),
        Cell('recorder', '记 录 人：', 3, 96.6 + GAP, 148.0, 78.9),
    ],
    topic_row=4, topic_x0=19.9 + GAP,
    extra_labels=[(4, 2.0, 19.9, '会议议题：')],
)

STUDY = BookForm(
    key='study', name='政治理论学习记录本',
    page_w=139.0, first_rows=21, first_pitch=204.0 / 20, body_start_row=6,
    body_w=139.0, body_h=204.0, body_lines=20,
    cells=[
        Cell('time', '学习时间：', 0, 19.8 + GAP, 68.5, 1.9),
        Cell('place', '学习地点：', 0, 89.8 + GAP, 138.0, 70.9),
        Cell('host', '主 持 人：', 1, 20.0 + GAP, 68.5, 1.9),
        Cell('recorder', '记 录 人：', 1, 89.8 + GAP, 138.0, 70.9),
        Cell('expected', '应到会人数：', 2, 20.2 + GAP, 68.5, 1.9),
        Cell('actual', '实到会人数：', 2, 90.0 + GAP, 138.0, 70.9),
        Cell('absent', '缺席人及原因：', 3, 24.0 + GAP, 138.0, 1.9, wrap_rows=[(4, 1.0, 138.0)]),
    ],
    extra_labels=[(5, 1.9, 16.7, '学习内容：')],
)

FORMS = {f.key: f for f in (MEETING, STUDY)}
