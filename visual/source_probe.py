"""一次性画面探测进程：阻塞的相机驱动由父进程超时终止。"""
import base64
import json
import os
import sys


def main():
    import cv2
    import numpy as np
    spec = json.load(sys.stdin)
    if spec['type'] == 'sim':
        frame = np.full((480, 640, 3), 55, np.uint8)
        cv2.putText(frame, 'DEMO INPUT', (150, 250), cv2.FONT_HERSHEY_SIMPLEX, 1, (220, 220, 220), 2)
    else:
        uri = spec.get('uri', 0)
        cap = cv2.VideoCapture(int(uri)) if spec['type'] == 'camera' else cv2.VideoCapture(
            uri, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 4000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 4000])
        try:
            ok, frame = cap.read()
            if not ok:
                raise ValueError('无法读取画面')
        finally:
            cap.release()
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (min(w, 960), max(1, int(h * min(w, 960) / w))))
    ok, jpg = cv2.imencode('.jpg', small)
    print(json.dumps({'width': w, 'height': h, 'image': base64.b64encode(jpg).decode()}))


if __name__ == '__main__':
    try:
        main()
        sys.stdout.flush()
        os._exit(0)  # 一次性进程已释放采集器，避免原生视频库退出析构卡住。
    except Exception:
        print(json.dumps({'error': '未读到画面，请检查设备供电、地址、账号及网络连接'}))
        sys.stdout.flush()
        os._exit(1)
