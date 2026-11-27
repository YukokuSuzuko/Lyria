"""A validated, disposable display projection; never the editable document."""
from copy import deepcopy

from lyricsync.api_alignment import accept_plan
from lyricsync.editing import History
from lyricsync.model import Project


class AlignmentPreview:
    def __init__(self):
        self.project=None
        self.context=None
        self.generation=0
        self.original_selection=''

    @staticmethod
    def identity(document,index):
        return (id(document.project),document.id,document.revision,id(index))

    @property
    def active(self):
        return self.project is not None

    def valid(self,document,index):
        return self.active and self.context==self.identity(document,index)

    def activate(self,document,index,plan,selected):
        if not plan or not plan.get('diff'):
            raise ValueError('暂无拆合建议，无法生成预览时间轴。')
        candidate=Project.from_dict(document.project.to_dict())
        # Reuse the exact adoption validation on an isolated project and history.
        # Tampered/stale plans fail before this object exposes any candidate.
        accept_plan(candidate,deepcopy(plan),index,History())
        self.project=candidate
        self.context=self.identity(document,index)
        self.original_selection=selected
        self.generation+=1
        return candidate

    def clear(self):
        selected=self.original_selection
        if self.active:self.generation+=1
        self.project=None;self.context=None;self.original_selection=''
        return selected
