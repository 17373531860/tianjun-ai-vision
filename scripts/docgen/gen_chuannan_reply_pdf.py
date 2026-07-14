# -*- coding: utf-8 -*-
"""川南火工反馈问题答复 → 美化 PDF。

风格取向: 供应商技术支持答复函 (克制的商务排版), 不做花哨封面。
产出: docs/川南火工问题答复_2026-07-13.pdf (随 .html 中间产物)
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.normpath(os.path.join(HERE, "..", "..", "docs"))
OUT_HTML = os.path.join(DOCS, "川南火工问题答复_2026-07-13.html")
OUT_PDF = os.path.join(DOCS, "川南火工问题答复_2026-07-13.pdf")

CSS = """
:root{--navy:#16324f; --cyan:#0e7490; --ink:#26303a; --muted:#66707a;
      --line:#dde3e9; --soft:#f4f7fa;}
*{box-sizing:border-box;}
@page{size:A4; margin:0;}
body{font-family:"Noto Sans CJK SC","Microsoft YaHei","WenQuanYi Zen Hei",sans-serif;
  color:var(--ink); margin:0; font-size:13.5px; line-height:1.78; background:#fff;}
.page{max-width:960px; margin:0 auto; padding:0 52px 40px;}

/* 抬头 */
.head{background:var(--navy); color:#fff; margin:0 -52px; padding:30px 52px 24px;}
.head .org{font-size:12px; letter-spacing:4px; opacity:.75; margin-bottom:10px;}
.head h1{font-size:26px; margin:0 0 6px; font-weight:700;}
.head .meta{font-size:12.5px; opacity:.85; margin-top:10px;}
.head .meta span{margin-right:26px;}

.lead{margin:22px 0 6px; color:var(--muted); font-size:13px;}

/* 问题块 */
.q{margin:26px 0 0; break-inside:avoid-page;}
.q .qt{border-left:4px solid var(--cyan); background:var(--soft);
  padding:9px 14px; font-weight:700; font-size:15px; color:var(--navy);
  border-radius:0 6px 6px 0; break-after:avoid; page-break-after:avoid;}
.q .qt .no{display:inline-block; color:var(--cyan); margin-right:8px;}
.q .body{padding:4px 2px 0;}

p{margin:9px 0;}
b{color:var(--navy);}
.path{background:#eef4f8; border:1px solid var(--line); border-radius:5px;
  padding:7px 12px; margin:10px 0; font-size:13px; color:var(--navy);}
.path b{color:var(--cyan);}
ul{margin:8px 0; padding-left:22px;}
li{margin:5px 0;}
.note{color:var(--muted); font-size:12.5px;}
.hr{border:none; border-top:1px solid var(--line); margin:22px 0 0;}
.ending{break-inside:avoid; page-break-inside:avoid;}
.close{margin-top:14px; color:var(--ink);}
.sign{margin-top:14px; text-align:right; color:var(--muted); font-size:13px; line-height:1.7;}
code{font-family:inherit; background:#eef4f8; border-radius:3px; padding:1px 5px;
  font-size:12.5px; color:var(--navy);}
"""

BODY = """
<div class="head">
  <div class="org">天军科技 · 技术支持</div>
  <h1>关于近期反馈问题的答复</h1>
  <div class="meta">
    <span>日期：2026-07-13</span>
    <span>贵方当前版本：v3.33</span>
    <span>修复发布版本：v3.37</span>
  </div>
</div>

<p class="lead">您上次提到的几个问题我们逐条核实过了，情况和处理办法如下。</p>

<div class="q">
  <div class="qt"><span class="no">一</span>Postman 发开工请求返回 OK，项目管理里切了项目，但检测中心界面没切换，也没直接进入推理状态</div>
  <div class="body">
    <p>这里其实是两件事。</p>
    <p><b>界面没跟着切换</b>——确认是我们的问题。v3.33 上后端收到开工后项目实际已经切换成功（所以您在项目管理里能看到结果），只是界面不会主动刷新显示。v3.37 已经修掉：开工切项目后，导航栏和检测中心会在 5 秒内自动跟上，不用刷新页面，切换时界面上会弹一条"项目已由外部系统切换"的提示。升级之前想立刻看到最新状态，手动按 F5 刷新一下就行。</p>
    <p><b>没直接进入推理状态</b>——这个是有意设计的默认行为，不是故障。开工指令默认只做"切项目 + 建任务"，要不要开始检测由现场决定，因为多数产线检测是常开的，开工只是热切换项目。贵方这个场景我们理解，v3.37 加了一个可选开关：</p>
    <div class="path">MES 管理 → 工单接收 → 收到任务后做什么 → <b>开工后自动开始检测</b></div>
    <p>打开后，开工处理成功时会对"视频源在跑、模型就绪、当前没在检测"的工位自动开始检测；条件不满足（比如相机没连、模型没加载）不影响开工响应本身，只在调试日志里记一笔。开关默认是关的，升级后需要在界面上勾一次并保存。</p>
  </div>
</div>

<div class="q">
  <div class="qt"><span class="no">二</span>海康工业相机取流，设置里只能选到 1080P，推理用的是 1080P 图像吗？</div>
  <div class="body">
    <p><b>推理用的是相机原生分辨率的完整图像，跟那个下拉没关系。</b></p>
    <p>具体说：海康工业相机走厂商 SDK 直连取流，用的是相机自身配置的分辨率，几百万像素就是几百万像素；设置里那个分辨率下拉只对 USB 摄像头生效，对工业相机不起作用。推理是在原生分辨率的完整帧上做的，按模型输入尺寸等比缩放送进模型，检测框坐标再映射回原图。检测中心那个画面是单独的一路预览，为了传输流畅做了压缩——显示分辨率不代表推理分辨率，不用担心精度。</p>
    <p class="note">如果要调相机本身的分辨率，在海康的 MVS 客户端里配置。</p>
  </div>
</div>

<div class="q">
  <div class="qt"><span class="no">三 / 七</span>开工报文里的任务代号、产品代号、工序工步、操作员，如何显示到检测中心主界面</div>
  <div class="body">
    <p>这个功能现在的版本就有（v3.29 起），不用等升级，配置两处：</p>
    <p>1）MES 管理 → 工单接收：打开<b>开工即建工单</b>；然后在<b>持续显示要素</b>里勾选要上屏的项（任务号 / 产品代号 / 工序工步 / 操作员，可任意组合，默认全不勾，勾了才显示）；点保存配置。</p>
    <p>2）回到检测中心，开始检测后，画面上方的任务信息条会一直显示这些要素，直到任务结束或被新任务顶替。</p>
    <p class="note">两个注意点：四要素取自开工报文本身，报文里要带对应字段（TaskNo / ProductCode / StepCode / Operator）；信息条在检测运行中才显示——如果开了第一条里说的自动开始检测，开工后就能直接看到。</p>
  </div>
</div>

<div class="q">
  <div class="qt"><span class="no">五</span>最新版本无法选择图像分割，名字还变成了"语义分割"</div>
  <div class="body">
    <p>确认是我们的界面缺陷，v3.37 已修。</p>
    <p>新建项目弹窗里，任务类型的第二个选项被误标成"语义分割"、还被误设成了不可选。软件的分割能力本身一直在、也一直正常（YOLO-seg 系列模型，输出每个目标独立的轮廓掩码），只是新建入口被封住了。修复后弹窗里"图像分割 (Instance Segmentation)"恢复可选。升级前如果着急用：先按"目标检测"建项目，再到项目配置页把任务类型改成图像分割，那个入口没受影响。</p>
    <p>顺带把用词说清楚：我们做的是<b>实例分割</b>——区分每个目标个体、各给一个轮廓，工业检测里数件、定位、逐件判定要的就是这个；不是学术上说的"语义分割"（只分像素类别、不区分个体）。之前那个选项名标错了。</p>
  </div>
</div>

<div class="q">
  <div class="qt"><span class="no">六</span>识别某个类别后框卡在画面上，后续类别也不识别了，导致 NG（3.19 没这个问题）</div>
  <div class="body">
    <p>这个我们做了两层处理，但根因还需要现场信息才能定。</p>
    <p>从现象判断，机制大概率是：推理链路发生持续异常时，最后一次的检测框会残留在画面上，看起来就是"框卡死、后面全不识别、周期超时 NG"。v3.37 起加了自愈防线——推理连续异常达到阈值时自动清空画面残留框（现场一眼能看出是链路故障，不会误以为是模型不识别），同时把异常详情记进调试日志中心。</p>
    <p>我们在和贵方场景同构的联调环境里（模拟中控推开工 + 连续检测）复测过推理链路，没能复现这个冻结现象，所以需要贵方配合取证。下面材料有哪个给哪个，越全越好：</p>
    <ul>
      <li><b>调试日志</b>：系统设置 → 调试日志，打开"检测推理"分类，复现一次问题后导出日志文件</li>
      <li><b>复现录屏</b>：从正常识别到框卡死的完整过程，手机拍屏也行</li>
      <li><b>模型信息</b>：所用模型文件（或说明模型类型、训练来源），以及推理格式是 PyTorch / ONNX / TensorRT 哪种</li>
      <li><b>触发规律</b>：是不是固定在识别某个类别之后出现？大概多久复现一次？</li>
    </ul>
    <p>拿到材料我们在同款模型同款配置下复现，给根治修复。</p>
  </div>
</div>

<div class="ending">
<hr class="hr"/>
<p class="close">以上修复都已经过完整回归验证，包括在模拟贵方中控的联调环境里真实跑通"中控推开工 → 项目自动切换 → 检测自动拉起 → 四要素上屏"的整条链路。有问题随时联系，需要远程协助配置也可以约时间。</p>
<div class="sign">天军科技 技术支持<br/>2026 年 7 月 13 日</div>
</div>
"""


def main():
    html = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<style>{CSS}</style></head><body><div class='page'>{BODY}</div></body></html>")
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print("HTML ->", OUT_HTML, os.path.getsize(OUT_HTML), "bytes")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page()
        pg.goto("file://" + OUT_HTML, wait_until="networkidle")
        pg.pdf(path=OUT_PDF, format="A4", print_background=True,
               margin={"top": "0", "bottom": "14mm", "left": "0", "right": "0"})
        b.close()
    print("PDF  ->", OUT_PDF, os.path.getsize(OUT_PDF), "bytes")


if __name__ == "__main__":
    main()
