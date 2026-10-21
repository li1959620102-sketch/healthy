/* ==========================================================================
 * 数字人形象渲染（Canvas 2D + 音频驱动口型）
 *
 * 为什么不用 3D 模型 / 视频素材：
 *   1. 体积。一个可用的 3D 数字人模型动辄几十 MB，还要 three.js + 骨骼动画，
 *      首页首屏会被拖垮；视频素材没法随回答内容变口型。
 *   2. 隐私与离线。形象驱动如果依赖云端渲染服务，用户的语音、回答文本都要出网，
 *      健康类产品不接受。
 *   3. 够用。用户需要的反馈是"它在听 / 它在想 / 它在说"，眼睛会眨、嘴会跟着
 *      音量开合，就已经比一张静态图强得多，成本却是零依赖的 Canvas 绘制。
 *
 * 口型怎么驱动：
 *   播报时用 WebAudio 的 AnalyserNode 取音频时域波形，算 RMS 得到当前音量，
 *   映射成嘴的开口高度。**不需要任何语音识别或音素对齐**——那套要么很贵，
 *   要么中文支持差；音量驱动的观感已经足够自然。
 *
 * 对外 API（无依赖、无构建，直接 <script> 引入）：
 *   var av = Avatar.create(canvasEl)     创建实例（同一 canvas 只创建一次）
 *   av.setState('idle'|'listening'|'thinking'|'speaking')
 *   av.speak(blobOrUrl)                  播放并驱动口型，返回 Promise
 *   av.stop()                            打断当前播放
 *   av.destroy()                         停止动画、释放 AudioContext
 * ========================================================================== */
(function (global) {
  'use strict';

  var PALETTE = {
    bg1: '#effffc', bg2: '#cdeee5',
    hair: '#243b4c', hairDark: '#1b2d3a',
    skin: '#f4d4bd', skinShade: '#e8bfa4',
    eye: '#27313a', blush: '#eec2b4',
    mouth: '#a45f61', brand: '#2f9e8f'
  };

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }

  /* 嘴的三种形态：闭合微笑 / 半开 / 全开。用一条二次曲线控制上下唇。 */
  function drawMouth(ctx, cx, cy, open) {
    var w = 11 + open * 3;             // 半宽
    var h = 1.2 + open * 9;            // 开口高度
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(cx - w, cy);
    ctx.quadraticCurveTo(cx, cy + h * 1.6, cx + w, cy);
    ctx.quadraticCurveTo(cx, cy - h * 0.35, cx - w, cy);
    ctx.closePath();
    ctx.fillStyle = open > 0.12 ? '#8d474a' : PALETTE.mouth;
    ctx.fill();
    if (open > 0.12) {
      // 露出的牙齿，让"张嘴"不像一个洞
      ctx.beginPath();
      ctx.moveTo(cx - w * 0.72, cy - 0.4);
      ctx.quadraticCurveTo(cx, cy + h * 0.5, cx + w * 0.72, cy - 0.4);
      ctx.quadraticCurveTo(cx, cy + h * 0.1, cx - w * 0.72, cy - 0.4);
      ctx.fillStyle = 'rgba(255,255,255,.85)';
      ctx.fill();
    }
    ctx.restore();
  }

  function drawEye(ctx, cx, cy, blink, r) {
    ctx.save();
    ctx.fillStyle = PALETTE.eye;
    if (blink > 0.72) {
      // 闭眼画成一条弧线，比把圆压扁自然
      ctx.lineWidth = 1.8;
      ctx.strokeStyle = PALETTE.eye;
      ctx.beginPath();
      ctx.arc(cx, cy + r * 0.4, r * 1.15, Math.PI * 1.15, Math.PI * 1.85);
      ctx.stroke();
    } else {
      ctx.beginPath();
      ctx.arc(cx, cy, r * (1 - blink * 0.85), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  var Avatar = {
    create: function (canvas, opts) {
      if (!canvas || !canvas.getContext) { return null; }
      opts = opts || {};
      var ctx = canvas.getContext('2d');
      var state = 'idle';
      var dpr = Math.min(global.devicePixelRatio || 1, 2);
      var W = opts.width || 140;
      var H = opts.height || 170;
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      canvas.style.width = W + 'px';
      canvas.style.height = H + 'px';
      ctx.scale(dpr, dpr);

      var t = 0;                      // 帧计数
      var blink = 0;                  // 0 睁眼 1 闭眼
      var nextBlink = 90 + Math.random() * 120;
      var open = 0;                   // 嘴开口 0~1
      var targetOpen = 0;
      var running = true;
      var audio = null;               // AudioContext
      var analyser = null;
      var sourceNode = null;
      var buf = null;                 // 当前波形数据
      var rafId = null;

      function analyserLevel() {
        if (!analyser || !buf) { return 0; }
        analyser.getByteTimeDomainData(buf);
        var sum = 0;
        for (var i = 0; i < buf.length; i++) {
          var v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        return Math.sqrt(sum / buf.length);
      }

      function frame() {
        if (!running) { return; }
        t++;

        /* --- 状态驱动的目标值 --- */
        if (state === 'speaking') {
          // 音量 → 开口。乘 4 是因为语音 RMS 通常只有 0.05~0.25，
          // 直接映射嘴几乎不动；开方让小声也有一点幅度。
          var level = analyserLevel();
          targetOpen = clamp(Math.pow(level * 4.2, 0.7), 0, 1);
        } else if (state === 'listening') {
          targetOpen = 0.12 + Math.sin(t / 9) * 0.06;   // 轻微起伏，表示在等你说
        } else if (state === 'thinking') {
          targetOpen = 0.05;
        } else {
          targetOpen = 0;
        }
        // 平滑，避免音量跳变导致嘴抖
        open += (targetOpen - open) * 0.35;

        /* --- 眨眼 --- */
        if (--nextBlink <= 0) {
          blink = Math.min(1, blink + 0.34);
          if (blink >= 1) { nextBlink = 110 + Math.random() * 150; }
        } else if (blink > 0) {
          blink = Math.max(0, blink - 0.22);
        }

        /* --- 绘制 --- */
        ctx.clearRect(0, 0, W, H);

        // 背景圆角胶囊 + 呼吸光晕
        var breathe = Math.sin(t / 46) * 2;
        ctx.save();
        var g = ctx.createLinearGradient(0, 0, 0, H);
        g.addColorStop(0, PALETTE.bg1);
        g.addColorStop(1, PALETTE.bg2);
        ctx.fillStyle = g;
        ctx.beginPath();
        roundRect(ctx, 6, 6 + breathe * 0.4, W - 12, H - 12, Math.min(W, H) * 0.42);
        ctx.fill();

        // 说话/聆听时的外圈脉冲
        if (state === 'speaking' || state === 'listening') {
          var pulse = (Math.sin(t / 12) + 1) / 2;
          ctx.beginPath();
          roundRect(ctx, 6, 6, W - 12, H - 12, Math.min(W, H) * 0.42);
          ctx.lineWidth = 2;
          ctx.strokeStyle = 'rgba(47,158,143,' + (0.16 + pulse * 0.34) + ')';
          ctx.stroke();
        }
        ctx.restore();

        // 头部整体随呼吸轻微上下
        var cy = H * 0.55 + breathe * 0.6;
        var cx = W / 2;
        var faceW = W * 0.52;
        var faceH = H * 0.44;

        // 肩颈：画成"衣领 + 肩膀"的整块，压在脸后面。
        // 只画一个矩形脖子会让下巴下方悬着一个方块，像贴了张纸条；
        // 半身像的常规做法是用肩线收口。
        ctx.save();
        ctx.fillStyle = '#bfe6dc';
        var shTop = cy + faceH * 0.58;
        ctx.beginPath();
        roundRect(ctx, cx - faceW * 0.78, shTop, faceW * 1.56, Math.max(10, (H - 10) - shTop), 14);
        ctx.fill();
        ctx.restore();

        // 头发（后层）
        ctx.save();
        ctx.fillStyle = PALETTE.hairDark;
        ctx.beginPath();
        ctx.ellipse(cx, cy - faceH * 0.34, faceW * 0.62, faceH * 0.6, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();

        // 脸
        ctx.save();
        ctx.fillStyle = PALETTE.skin;
        ctx.beginPath();
        ctx.ellipse(cx, cy, faceW * 0.5, faceH * 0.52, 0, 0, Math.PI * 2);
        ctx.fill();
        // 腮红
        ctx.fillStyle = 'rgba(238,194,180,.55)';
        ctx.beginPath();
        ctx.ellipse(cx - faceW * 0.28, cy + faceH * 0.16, 7, 4.4, 0, 0, Math.PI * 2);
        ctx.ellipse(cx + faceW * 0.28, cy + faceH * 0.16, 7, 4.4, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();

        // 刘海（前层）
        ctx.save();
        ctx.fillStyle = PALETTE.hair;
        ctx.beginPath();
        ctx.ellipse(cx, cy - faceH * 0.5, faceW * 0.52, faceH * 0.33, 0, Math.PI, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.moveTo(cx - faceW * 0.52, cy - faceH * 0.5);
        ctx.quadraticCurveTo(cx - faceW * 0.1, cy - faceH * 0.2, cx + faceW * 0.18, cy - faceH * 0.52);
        ctx.quadraticCurveTo(cx + faceW * 0.02, cy - faceH * 0.34, cx - faceW * 0.24, cy - faceH * 0.28);
        ctx.closePath();
        ctx.fill();
        ctx.restore();

        // 眼睛（眨眼 + 思考时向上看）
        var lookUp = state === 'thinking' ? -1.6 : 0;
        drawEye(ctx, cx - faceW * 0.21, cy - faceH * 0.02 + lookUp, blink, 3.1);
        drawEye(ctx, cx + faceW * 0.21, cy - faceH * 0.02 + lookUp, blink, 3.1);

        // 眉毛：思考时微皱
        ctx.save();
        ctx.strokeStyle = PALETTE.hair;
        ctx.lineWidth = 1.6;
        ctx.lineCap = 'round';
        var browY = cy - faceH * 0.2 - (state === 'thinking' ? 1.2 : 0);
        ctx.beginPath();
        ctx.moveTo(cx - faceW * 0.3, browY + 1.5);
        ctx.quadraticCurveTo(cx - faceW * 0.21, browY, cx - faceW * 0.12, browY + 1.5);
        ctx.moveTo(cx + faceW * 0.12, browY + 1.5);
        ctx.quadraticCurveTo(cx + faceW * 0.21, browY, cx + faceW * 0.3, browY + 1.5);
        ctx.stroke();
        ctx.restore();

        // 嘴
        drawMouth(ctx, cx, cy + faceH * 0.28, open);

        // 思考中的三点提示，放在形象下方
        if (state === 'thinking') {
          ctx.save();
          for (var i = 0; i < 3; i++) {
            var a = (Math.sin(t / 8 - i * 0.9) + 1) / 2;
            ctx.beginPath();
            ctx.arc(cx - 9 + i * 9, H - 16, 2.2, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(47,158,143,' + (0.25 + a * 0.6) + ')';
            ctx.fill();
          }
          ctx.restore();
        }

        rafId = global.requestAnimationFrame(frame);
        return;
      }

      function roundRect(c, x, y, w, h, r) {
        c.beginPath();
        c.moveTo(x + r, y);
        c.arcTo(x + w, y, x + w, y + h, r);
        c.arcTo(x + w, y + h, x, y + h, r);
        c.arcTo(x, y + h, x, y, r);
        c.arcTo(x, y, x + w, y, r);
        c.closePath();
      }

      function ensureAudio() {
        if (audio) { return audio; }
        var Ctor = global.AudioContext || global.webkitAudioContext;
        if (!Ctor) { return null; }
        audio = new Ctor();
        analyser = audio.createAnalyser();
        analyser.fftSize = 512;
        buf = new Uint8Array(analyser.fftSize);
        analyser.connect(audio.destination);
        return audio;
      }

      function stopPlayback() {
        try {
          if (sourceNode) { sourceNode.onended = null; sourceNode.stop(0); }
        } catch (e) { /* 已结束 */ }
        sourceNode = null;
        targetOpen = 0;
      }

      var instance = {
        canvas: canvas,

        setState: function (s) {
          if (['idle', 'listening', 'thinking', 'speaking'].indexOf(s) < 0) { return; }
          state = s;
          if (s !== 'speaking') { targetOpen = 0; }
        },

        getState: function () { return state; },

        /* 播放一段音频并驱动口型。接受 Blob（TTS 直接返回的 mp3）或 URL。 */
        speak: function (src) {
          var ac = ensureAudio();
          if (!ac) { return Promise.reject(new Error('浏览器不支持 WebAudio')); }
          // 浏览器要求用户手势后才能播放，这里在点击链路里调用，顺带 resume
          var ready = ac.state === 'suspended' ? ac.resume() : Promise.resolve();
          return ready.then(function () {
            return fetch(src instanceof Blob ? URL.createObjectURL(src) : src);
          }).then(function (r) {
            if (!r.ok) { throw new Error('音频获取失败 HTTP ' + r.status); }
            return r.arrayBuffer();
          }).then(function (ab) {
            return ac.decodeAudioData(ab);
          }).then(function (decoded) {
            stopPlayback();
            sourceNode = ac.createBufferSource();
            sourceNode.buffer = decoded;
            sourceNode.connect(analyser);
            state = 'speaking';
            return new Promise(function (resolve) {
              sourceNode.onended = function () {
                if (state === 'speaking') { state = 'idle'; }
                targetOpen = 0;
                resolve(true);
              };
              sourceNode.start(0);
            });
          });
        },

        stop: function () {
          stopPlayback();
          if (state === 'speaking') { state = 'idle'; }
        },

        destroy: function () {
          running = false;
          if (rafId) { global.cancelAnimationFrame(rafId); }
          stopPlayback();
          if (audio && audio.close) { try { audio.close(); } catch (e) { /* ignore */ } }
          audio = null; analyser = null; buf = null;
        }
      };

      rafId = global.requestAnimationFrame(frame);
      return instance;
    }
  };

  global.Avatar = Avatar;
})(window);
