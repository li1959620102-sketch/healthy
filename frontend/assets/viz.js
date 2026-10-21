/* ============================================================
 * 轻量可视化组件（无第三方依赖、无构建）
 * ------------------------------------------------------------
 * 为什么自己写而不是引 ECharts：前端是"同源托管 + 无打包"的原生页面，
 * 引一个 1MB 的图表库只为画 7 个点不划算，而且内网/离线环境取不到 CDN。
 * 这里只做本平台真正用到的四种图：面积折线、环形、迷你走势、数值滚动。
 *
 * 约定：
 *   - 所有颜色取自 CSS 变量，深浅色主题切换无需改 JS；
 *   - 所有图形都带 <title>，鼠标悬停可读到原始数值；
 *   - 数值一律由调用方传入，本模块不造数、不补零；
 *   - 尊重系统"减少动效"偏好，开启时不做入场动画。
 * ============================================================ */
(function (global) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var reduceMotion = global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    v = (v || '').trim();
    return v || fallback;
  }

  function el(tag, attrs) {
    var node = document.createElementNS(NS, tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }

  function num(v) {
    var n = Number(v);
    return isFinite(n) ? n : 0;
  }

  /* 单调三次插值（Monotone cubic）：比普通贝塞尔更不会在两点之间"冲高"，
     数据是真实业务量时，曲线不能画到实际最大值以上，否则读者会误读。 */
  function smoothPath(pts) {
    if (pts.length === 1) return 'M' + pts[0][0] + ',' + pts[0][1];
    var d = 'M' + pts[0][0] + ',' + pts[0][1];
    for (var i = 0; i < pts.length - 1; i++) {
      var p0 = pts[i - 1] || pts[i];
      var p1 = pts[i];
      var p2 = pts[i + 1];
      var p3 = pts[i + 2] || p2;
      var t = 0.22;
      var c1x = p1[0] + (p2[0] - p0[0]) * t;
      var c1y = p1[1] + (p2[1] - p0[1]) * t;
      var c2x = p2[0] - (p3[0] - p1[0]) * t;
      var c2y = p2[1] - (p3[1] - p1[1]) * t;
      d += 'C' + c1x.toFixed(1) + ',' + c1y.toFixed(1) + ' ' +
        c2x.toFixed(1) + ',' + c2y.toFixed(1) + ' ' +
        p2[0].toFixed(1) + ',' + p2[1].toFixed(1);
    }
    return d;
  }

  /* 轴上限取"比最大值大一档的整数刻度"，而不是 10 的整数次幂。
     原来 705 会被拉到 1000，曲线被迫压在下半张图里，看不出起伏。 */
  var NICE_STEPS = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
  function niceMax(v) {
    if (v <= 0) return 1;
    var exp = Math.pow(10, Math.floor(Math.log(v) / Math.LN10));
    var f = v / exp;
    for (var i = 0; i < NICE_STEPS.length; i++) {
      if (f <= NICE_STEPS[i] + 1e-9) return NICE_STEPS[i] * exp;
    }
    return 10 * exp;
  }

  /* ---------------------------------------------------------- *
   * 1) 面积折线图
   * opts: { points:[{label,value,tip}], height, unit, color, title }
   * ---------------------------------------------------------- */
  var areaBoxes = [];

  function area(box, opts) {
    if (!box) return;
    opts = opts || {};
    var pts = (opts.points || []).filter(function (p) { return p && isFinite(Number(p.value)); });
    if (!pts.length) { box.innerHTML = ''; return; }

    // 记住入参：窗口尺寸变化时按新宽度重画（矢量图拉伸会让圆点变椭圆、线变粗）
    box.__vizArea = opts;
    if (areaBoxes.indexOf(box) < 0) areaBoxes.push(box);

    var H = opts.height || 260;
    var W = Math.max(320, Math.round(box.clientWidth || 760));
    var padL = 46, padR = 18, padT = 22, padB = 36;
    var innerW = W - padL - padR, innerH = H - padT - padB;
    var maxV = niceMax(Math.max.apply(null, pts.map(function (p) { return num(p.value); })));
    var color = opts.color || cssVar('--brand', '#16a085');
    var grid = cssVar('--line', '#e7ecf3');
    var muted = cssVar('--muted', '#667085');

    var stepX = pts.length > 1 ? innerW / (pts.length - 1) : 0;
    var coords = pts.map(function (p, i) {
      return [padL + stepX * i, padT + innerH - (num(p.value) / maxV) * innerH];
    });

    var svg = el('svg', {
      viewBox: '0 0 ' + W + ' ' + H, width: '100%', height: H,
      role: 'img',
      'aria-label': (opts.title || '趋势图') + '：共 ' + pts.length + ' 个数据点'
    });
    svg.style.display = 'block';

    var gid = 'vz' + Math.random().toString(36).slice(2, 8);
    var defs = el('defs');
    var grad = el('linearGradient', { id: gid, x1: '0', y1: '0', x2: '0', y2: '1' });
    grad.appendChild(el('stop', { offset: '0%', 'stop-color': color, 'stop-opacity': '0.26' }));
    grad.appendChild(el('stop', { offset: '100%', 'stop-color': color, 'stop-opacity': '0' }));
    defs.appendChild(grad);
    svg.appendChild(defs);

    // 网格：4 条横线，右侧标数值。不画竖线，避免和柱状图的观感混淆
    for (var g = 0; g <= 4; g++) {
      var y = padT + innerH - (innerH / 4) * g;
      svg.appendChild(el('line', {
        x1: padL, y1: y, x2: W - padR, y2: y,
        stroke: grid, 'stroke-width': g === 0 ? 1.2 : 1,
        'stroke-dasharray': g === 0 ? '0' : '3 5'
      }));
      var label = el('text', {
        x: padL - 10, y: y + 4, 'text-anchor': 'end',
        fill: muted, 'font-size': '11', 'font-family': 'inherit'
      });
      label.textContent = Math.round(maxV / 4 * g).toLocaleString();
      svg.appendChild(label);
    }

    var line = smoothPath(coords);
    var areaPath = line + 'L' + coords[coords.length - 1][0].toFixed(1) + ',' + (padT + innerH) +
      'L' + coords[0][0].toFixed(1) + ',' + (padT + innerH) + 'Z';

    var fill = el('path', { d: areaPath, fill: 'url(#' + gid + ')', stroke: 'none' });
    var stroke = el('path', {
      d: line, fill: 'none', stroke: color, 'stroke-width': 2.2,
      'stroke-linecap': 'round', 'stroke-linejoin': 'round'
    });

    if (!reduceMotion) {
      fill.style.opacity = '0';
      fill.style.transition = 'opacity .6s ease .25s';
      var len = 0;
      try { len = stroke.getTotalLength(); } catch (e) { len = 0; }
      if (len) {
        stroke.style.strokeDasharray = len;
        stroke.style.strokeDashoffset = len;
        stroke.style.transition = 'stroke-dashoffset .9s ease';
      }
      requestAnimationFrame(function () {
        fill.style.opacity = '1';
        if (len) stroke.style.strokeDashoffset = '0';
      });
    }

    svg.appendChild(fill);
    svg.appendChild(stroke);

    // 数据点 + 悬停提示；点数多时隔点显示，避免标签糊成一片
    var labelEvery = Math.ceil(pts.length / 7);
    coords.forEach(function (c, i) {
      var dot = el('circle', {
        cx: c[0], cy: c[1], r: 3.4, fill: '#fff', stroke: color, 'stroke-width': 2
      });
      var tip = el('title');
      tip.textContent = pts[i].tip ||
        (pts[i].label + '：' + num(pts[i].value).toLocaleString() + (opts.unit || ''));
      dot.appendChild(tip);
      svg.appendChild(dot);

      if (i % labelEvery === 0 || i === coords.length - 1) {
        var t = el('text', {
          x: c[0], y: H - 12, 'text-anchor': 'middle',
          fill: muted, 'font-size': '11', 'font-family': 'inherit'
        });
        t.textContent = pts[i].label;
        svg.appendChild(t);
      }
    });

    box.innerHTML = '';
    box.appendChild(svg);
  }

  /* 窗口尺寸变化时重画所有面积图（防抖 160ms，避免拖动窗口时反复重排） */
  var resizeTimer = null;
  global.addEventListener('resize', function () {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      areaBoxes.forEach(function (b) {
        if (b && b.__vizArea && b.clientWidth) area(b, b.__vizArea);
      });
    }, 160);
  });

  /* ---------------------------------------------------------- *
   * 2) 环形占比
   * opts: { value, max, title, sub, color, size }
   * ---------------------------------------------------------- */
  function donut(box, opts) {
    if (!box) return;
    opts = opts || {};
    var value = num(opts.value);
    var max = num(opts.max) || 100;
    var pct = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
    var size = opts.size || 190;
    var r = size / 2 - 14;
    var circ = 2 * Math.PI * r;
    var color = opts.color || cssVar('--brand', '#16a085');

    var svg = el('svg', { viewBox: '0 0 ' + size + ' ' + size, width: size, height: size, role: 'img' });
    svg.setAttribute('aria-label', (opts.title || '占比') + '：' + (pct * 100).toFixed(1) + '%');
    svg.appendChild(el('circle', {
      cx: size / 2, cy: size / 2, r: r, fill: 'none',
      stroke: cssVar('--line', '#e7ecf3'), 'stroke-width': 14
    }));
    var arc = el('circle', {
      cx: size / 2, cy: size / 2, r: r, fill: 'none',
      stroke: color, 'stroke-width': 14, 'stroke-linecap': 'round',
      'stroke-dasharray': circ, 'stroke-dashoffset': circ,
      transform: 'rotate(-90 ' + (size / 2) + ' ' + (size / 2) + ')'
    });
    svg.appendChild(arc);

    var t1 = el('text', {
      x: size / 2, y: size / 2 + 2, 'text-anchor': 'middle',
      fill: cssVar('--ink', '#172033'), 'font-size': '26', 'font-weight': '800',
      'font-family': 'inherit', 'letter-spacing': '-.02em'
    });
    t1.textContent = (pct * 100).toFixed(1) + '%';
    svg.appendChild(t1);
    if (opts.sub) {
      var t2 = el('text', {
        x: size / 2, y: size / 2 + 22, 'text-anchor': 'middle',
        fill: cssVar('--muted', '#667085'), 'font-size': '11', 'font-family': 'inherit'
      });
      t2.textContent = opts.sub;
      svg.appendChild(t2);
    }

    box.innerHTML = '';
    box.appendChild(svg);

    requestAnimationFrame(function () {
      arc.style.transition = reduceMotion ? 'none' : 'stroke-dashoffset 1s cubic-bezier(.22,.61,.36,1)';
      arc.style.strokeDashoffset = String(circ * (1 - pct));
    });
    return svg;
  }

  /* ---------------------------------------------------------- *
   * 3) 迷你走势线（KPI 卡右下角那种）
   * ---------------------------------------------------------- */
  function spark(box, opts) {
    if (!box) return;
    opts = opts || {};
    var vals = (opts.values || []).map(num);
    if (vals.length < 2) { box.innerHTML = ''; return; }
    var W = 108, H = 34, pad = 3;
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    var span = max - min || 1;
    var color = opts.color || cssVar('--brand', '#16a085');
    var stepX = (W - pad * 2) / (vals.length - 1);
    var coords = vals.map(function (v, i) {
      return [pad + stepX * i, H - pad - ((v - min) / span) * (H - pad * 2)];
    });
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, width: W, height: H, role: 'img' });
    svg.setAttribute('aria-label', '近况走势');
    svg.appendChild(el('path', {
      d: smoothPath(coords), fill: 'none', stroke: color,
      'stroke-width': 1.8, 'stroke-linecap': 'round'
    }));
    var last = coords[coords.length - 1];
    svg.appendChild(el('circle', { cx: last[0], cy: last[1], r: 2.6, fill: color }));
    box.innerHTML = '';
    box.appendChild(svg);
  }

  /* ---------------------------------------------------------- *
   * 4) 数值滚动：从 0 滚到目标值
   * 只对"能确定目标值"的数字用；未知（—）直接跳过，不伪造过渡。
   * ---------------------------------------------------------- */
  function countUp(node, opts) {
    if (!node) return;
    opts = opts || {};
    var to = Number(opts.to);
    if (!isFinite(to)) return;
    var decimals = opts.decimals || 0;
    var suffix = opts.suffix || '';
    var prefix = opts.prefix || '';
    var fmt = function (v) {
      return prefix + (decimals
        ? v.toFixed(decimals)
        : Math.round(v).toLocaleString()) + suffix;
    };
    if (reduceMotion) { node.textContent = fmt(to); return; }

    var from = Number(opts.from);
    if (!isFinite(from)) from = 0;
    var dur = opts.duration || 620;
    var t0 = null;
    function tick(ts) {
      if (t0 === null) t0 = ts;
      var p = Math.min(1, (ts - t0) / dur);
      var eased = 1 - Math.pow(1 - p, 3);
      node.textContent = fmt(from + (to - from) * eased);
      if (p < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  /* 骨架屏：加载期间占位，避免"闪一下空卡片" */
  function skeleton(box, rows) {
    if (!box) return;
    rows = rows || 3;
    var out = '';
    for (var i = 0; i < rows; i++) out += '<div class="sk-line" style="width:' + (94 - i * 12) + '%"></div>';
    box.innerHTML = '<div class="sk">' + out + '</div>';
  }

  global.Viz = { area: area, donut: donut, spark: spark, countUp: countUp, skeleton: skeleton };
})(window);
