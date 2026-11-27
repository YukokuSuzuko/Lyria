"""Offline packaged-app workflow diagnostic, operating only on a copied project."""
from pathlib import Path
import tkinter as tk
from .model import Project
from .gui import Application
from .storage import load_project,atomic_json,save_project,file_hash
from .vision import Video
from .frames import FrameIndex
from .editing import split_mapping,merge_mappings,mapping_text
from .export import export_project,validate_project
from .audio import prepare_audio
from .tk_lifecycle import destroy_root


def check(project_path,folder):
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    original=load_project(project_path)
    before=file_hash(project_path)
    root=tk.Tk();root.withdraw();root.update_idletasks()
    app=Application(root,folder/'ui-data')
    try:
        p=Project.from_dict(original.to_dict())
        # Two existing timed cues only: this is not a full-song or semantic pass.
        rows=[m for m in p.mappings if m.start is not None and m.end is not None][:2]
        if len(rows)<2:raise ValueError('测试工程需要至少两条已有时间的字幕')
        # This copied fixture exercises review navigation. The user's original
        # cues may already be confirmed and must then be skipped by confirm-next.
        for row in rows:
            row.locked=False;row.confirmed=False
            row.reasons=['验证副本：待人工确认']
        first_id,second_id=rows[0].id,rows[1].id
        p.mappings=rows;tids={i for m in rows for i in m.translation_ids}
        p.translations=[t for t in p.translations if t.id in tids]
        app.project=p;app.project_path=folder/'two_cues_test.lyricsync.json'
        for key,var in app.inputs.items():var.set(p.inputs.get(key,''))
        app.preview_video=Video(p.inputs['video']);app.frame_index=FrameIndex.build(app.preview_video)
        app.timeline.configure(to=app.preview_video.info['duration'])
        app.refresh();app.results.selection_set(rows[0].id);app.select_mapping()
        m=app.selected_mapping();text=mapping_text(p,m);outer=(m.start,m.end)
        app.history.push(p,'workflow split')
        cut=app.frame_index.stamp(app.frame_index.current((m.start+m.end)/2))
        a,b=split_mapping(p,m.id,max(1,len(text)//2),cut)
        assert mapping_text(p,a)+mapping_text(p,b)==text
        merged=merge_mappings(p,{a.id,b.id})
        assert mapping_text(p,merged)==text and (merged.start,merged.end)==outer
        app.undo();assert len(app.project.mappings)==2
        app.results.selection_set(first_id);app.select_mapping()
        app.confirm_next();assert app.selected_mapping().id==second_id,'确认第一条后未跳到待确认的第二条'
        app.confirm_next();assert not app.project.unresolved(),'两条验证字幕尚未完成确认'
        output=folder/'two_cues_功能验证_非整首.ass'
        export_project(app.project,output)
        app.audio_path=prepare_audio(app.preview_video,folder/'audio')
        app.audio_state='声音就绪' if app.audio_path else '视频无音轨'
        app.seek(1);app.toggle_play();root.after(450,root.quit);root.mainloop()
        audio_check=None
        if app.audio_path:
            assert app.audio_playing and app.audio.position()>1.1
            audio_check={'position':app.audio.position(),'video_pts':app.stamp}
            assert abs(audio_check['position']-app.stamp)<.3
        assert app.stamp>1.1;app.pause_playback();stamp=app.stamp
        app.step(-1);assert app.stamp<stamp
        first=app.frame_index.current(app.stamp)
        app.request_step(5);app.request_step(5);app.request_step(-1)
        root.after(400,root.quit);root.mainloop()
        assert app.frame_index.current(app.stamp)==first+9,'连续帧请求丢失或未应用'
        step_check={'first':first,'final':app.frame_index.current(app.stamp),'delta':9}
        # End-limited audio and video, including a very short cue.
        app.seek(1);app.toggle_play(stop_at=1.2)
        root.after(550,root.quit);root.mainloop()
        assert not app.playing and not app.audio_playing,'本句播放结束后未停止'
        assert 1<=app.stamp<1.2,'本句播放超过指定结束时间'
        app.begin_timeline_drag();app.timeline.set(1.8)
        app.stamp=1.2;app.sync_playhead()
        assert abs(float(app.timeline.get())-1.8)<1e-6,'旧画面把拖动滑块拉回'
        app.end_timeline_drag();root.after(400,root.quit);root.mainloop()
        assert abs(app.stamp-1.8)<.05 and abs(float(app.timeline.get())-app.stamp)<1e-6
        app.project_saver.flush()
        save_project(app.project,app.project_path)
        assert file_hash(project_path)==before
        atomic_json(folder/'workflow_check.json',dict(ok=True,scope='two copied real-video cues; not full-song semantic verification',
            frames=len(app.frame_index.stamps),source_project_unchanged=True,
            checks=['actual Tk GUI','frame index','split','merge','undo','confirm-next','ASS export','play-pause','previous-frame','queued-frame-steps','end-limited-playback','timeline-thumb-ownership'],audio=audio_check,frame_steps=step_check,
            remaining_full_project_issues=len(original.unresolved()),ass=str(output)))
    finally:
        app.project_saver.close()
        app.pause_playback();app.audio.close();app.index_generation+=1
        destroy_root(root)
