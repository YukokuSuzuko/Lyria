"""One isolated OCR provider per task. The parent controls the process and its duplex pipe."""
import multiprocessing
import threading
import weakref


def serve(conn, profile, attestation, settings):
    from . import split_profiles as profiles
    from . import shared_runtime as shared
    from .vision import RapidOCRProvider
    try:
        app = shared.application_dir()
        if profile == 'nvidia':
            info = shared._header(app, 'nvidia')
            if (info['identity'] != attestation['manifest_sha256'] or
                    shared._snapshot(info) != attestation['metadata_sha256']):
                raise ValueError('已校验的 NVIDIA 运行库发生变化')
            site = info['root']/'Lib/site-packages'
            profiles.load_ort(site, site/'torch/lib')
        elif profile in ('directml', 'amd'):
            profiles.load_ort(profiles.dml_root())
        else:
            profiles.load_ort(app.parent/'shared-runtime/Lib/site-packages')
        engine = RapidOCRProvider(**settings)
        conn.send((True, engine.signature))
        while True:
            image = conn.recv()
            if image is None:
                break
            detections = engine.recognize(image)
            conn.send((True, (detections,engine.signature)))
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try:
            conn.send((False, str(exc)))
        except (EOFError,BrokenPipeError,OSError):
            pass
    finally:
        conn.close()


def stop_worker(pipe, process):
    pipe.close()
    process.join(.4)
    if process.is_alive():
        process.terminate()
        process.join(2)


class WorkerUnavailable(RuntimeError):
    pass


class IsolatedOCR:
    name = 'rapidocr'

    def __init__(self, **settings):
        from . import split_profiles as profiles
        selected = profiles.ACTIVE
        requested = settings.get('device','cpu')
        profile = selected if requested in ('auto', {'nvidia':'cuda','amd':'directml','directml':'directml'}.get(selected)) else 'cpu'
        self._lock = threading.Lock()
        self._settings = dict(settings)
        with profiles.ocr_priority():
            self._launch(profile, profiles.OCR_ATTESTATION, settings)
        if self.signature.get('fallback_reason'):
            profiles.OCR_NOTICE = self.signature['fallback_reason']

    def _launch(self, profile, attestation, settings):
        ctx = multiprocessing.get_context('spawn')
        self.pipe, child = ctx.Pipe()
        process = ctx.Process(target=serve,args=(child,profile,attestation,settings),daemon=True)
        try:
            process.start()
        except Exception:
            child.close()
            self.pipe.close()
            raise
        child.close()
        self.process = process
        self.finish = weakref.finalize(self,stop_worker,self.pipe,process)
        try:
            self.signature = self.receive()
        except Exception:
            self.finish()
            if profile == 'cpu':
                raise
            self._launch('cpu',None,dict(settings,device='cpu'))
            self.signature['fallback_reason'] = '显卡 OCR 工作进程未能启动，本次任务使用 CPU。'

    def receive(self):
        from multiprocessing.connection import wait
        ready = wait([self.pipe, self.process.sentinel], timeout=120)
        if not ready:
            raise WorkerUnavailable('OCR 工作进程响应超时')
        if self.pipe not in ready:
            raise WorkerUnavailable('OCR 工作进程已退出。')
        try:
            ok,value = self.pipe.recv()
        except EOFError as exc:
            raise WorkerUnavailable('OCR 工作进程意外退出。') from exc
        if not ok:
            raise RuntimeError(value)
        return value

    def recognize(self, image):
        from . import split_profiles as profiles
        with self._lock, profiles.ocr_priority():
            try:
                self.pipe.send(image)
                result, signature = self.receive()
            except (OSError, WorkerUnavailable):
                if self.signature.get('device') == 'cpu':
                    raise
                previous = self.signature
                self.finish()
                self._launch('cpu',None,dict(self._settings,device='cpu'))
                previous.update(self.signature)
                self.signature = previous
                self.signature['fallback_reason'] = '显卡 OCR 工作进程中断，本次任务已改用 CPU。'
                profiles.OCR_NOTICE = self.signature['fallback_reason']
                self.pipe.send(image)
                result, signature = self.receive()
            notice = signature.get('fallback_reason') or self.signature.get('fallback_reason')
            self.signature.update(signature)
            if notice:
                self.signature['fallback_reason'] = notice
                profiles.OCR_NOTICE = notice
            return result
