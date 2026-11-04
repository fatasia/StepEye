# VideoMAEv2 ONNX CUDA

动作旁路现可使用独立CUDA环境；主服务仍用原CPU环境，不导入Torch、不改变实时YOLO配置。

## 部署

先建立独立环境，安装 `sop-vision/requirements-action-cuda.txt`。ORT 1.27默认GPU轮子对应CUDA13/cuDNN9，不能与CUDA12的DLL混用；版本依据[官方CUDA依赖表](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html#requirements)。需已有兼容CUDA/cuDNN运行库；没有时按官方说明安装对应运行库，不复制整套训练环境进基础安装包。

Windows本机复用已安装的CUDA13/cuDNN9 DLL目录，不导入Torch：

```powershell
$env:STEPEYE_ACTION_PYTHON='D:\Documents\ChatGPT\visoul\.v0\action-cuda-venv\Scripts\python.exe'
$env:STEPEYE_ACTION_DLL_DIRECTORY='C:\Users\rain\anaconda3\Lib\site-packages\torch\lib'
```

这两个路径需替换为部署机实际路径。Linux使用独立环境的python与正确安装的CUDA/cuDNN库；本次仅Windows实际运行，未做Linux GPU验收。配置来源仅为管理员进程环境，不接受HTTP上传可执行路径。

工位保留原 `action_shadow` 模型路径、710类映射与SHA，改为 `providers: [CUDAExecutionProvider]` 即使用严格CUDA执行。默认仍关闭；不自动修改现有工位。

- `STEPEYE_ACTION_PYTHON`现在传到已有按需worker，调度器切环境会重新加载，避免配置了CUDA却仍在CPU Python中运行。
- CUDA-only设置禁用CPU算子回退，且关闭ORT运行时EP失败回退；无法初始化或执行时返回未知/错误，不偷偷改用CPU。
- 若管理员显式填写CUDA+CPU，允许混合节点执行；不得把它标成全CUDA。ORT的provider列表可能仍列出CPU，实际节点以profile为准。
- 模型加载前复用全局 `STEPEYE_MODEL_TRUST_KEYS` 签名策略；只验证一次，worker继续核SHA，不逐帧验签。
- 空闲退出、过期结果丢弃、有界最新窗口队列仍复用原实现；不新增常驻线程。

## 实际结果

本机RTX4060 Laptop 8GB，Python3.13.9 / ORT GPU1.27.0；复用原固定16帧224、batch1模型：

`6748dbc33dfb4f83c581bfdd695c99c4f83654d63e87867d8b289f7fb40558a2`

| 指标 | 实测 |
|---|---:|
| worker启动与模型加载 | 755.75ms |
| 首次请求往返 | 204.30ms |
| 热态往返P50/P95（5次） | 44.59 / 74.22ms |
| 热态纯推理P50/P95（5次） | 26.22 / 26.98ms |
| profile内CUDA/CPU节点 | 319 / 0 |
| CPU/CUDA logits最大绝对差 | 0.00274527 |
| CPU/CUDA目标置信度 | 0.718298 / 0.718577 |

同一公开片段均输出class30「eating spaghetti」。使用原FP32 ONNX与CUDA默认数值路径，没有重新导出FP16/INT8；这不是工业动作准确率或多路容量结论。首次完整启动至结果约960ms，冷启动仍显著慢于热态；不要把74ms解释为动作开始后74ms便能识别，采样窗口本身还需16帧。

现有ActionScheduler真实CUDA smoke：28次按时间输入已有帧、接受2条结果、错误0；observe调用P95 1.48ms。测试用0.2秒idle加速退出，因此发生2次冷启动；所有自有worker及scheduler均已退出。没有测关闭旁路的对照吞吐，不宣称主链增幅小于5%。

证据：`.v0/action-cuda-probe-v2/report.json`及原始profile、`.v0/videomaev2-ort-cuda-bench.json`、`.v0/action-cuda-shadow-smoke.json`。初版探针错误地要求provider列表只有CUDA，在推理前终止；修正为profile节点核查后完成上述实测，失败日志保留。

## 复现

`tools/probe_action_cuda.py`做单片段CPU/CUDA数值及节点核查；`tools/bench_action.py`继续测真实worker，不造新性能框架。输出目录/报告存在时拒绝覆盖。

```powershell
# 先设置上面的DLL目录环境变量
.v0\action-cuda-venv\Scripts\python.exe sop-vision/tools/probe_action_cuda.py --model sop-vision/models/action/videomaev2-small-k710.onnx --clip .v0/action-base/clip.npy --classes sop-vision/models/action/videomaev2-small-k710.classes.json --sha256 6748dbc33dfb4f83c581bfdd695c99c4f83654d63e87867d8b289f7fb40558a2 --output .v0/my-cuda-probe
```

工业动作头、现场工步真值和多工位容量仍未补齐；通用K710分类只保留影子用途。
