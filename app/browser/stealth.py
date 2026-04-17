"""JS patches injected into every page to hide obvious automation signals.

Multi-hunter and similar anti-bot stacks check a handful of well-known leaks:
`navigator.webdriver`, missing `window.chrome`, inconsistent WebGL vendor,
no plugins, permissions API lies, stack traces mentioning Playwright, etc.

This module builds one init script per Fingerprint and returns it as a string
suitable for `context.add_init_script(script)`. It runs before any page script,
so by the time the game probes these values, they already look normal.
"""
from __future__ import annotations

import json

from app.browser.fingerprint import Fingerprint


def build_init_script(fp: Fingerprint) -> str:
    sw, sh = fp.screen
    languages_js = json.dumps([fp.locale, fp.locale.split("-")[0]])
    platform_js = json.dumps(fp.platform)
    webgl_vendor_js = json.dumps(fp.webgl_vendor)
    webgl_renderer_js = json.dumps(fp.webgl_renderer)

    return f"""
(() => {{
  Object.defineProperty(Navigator.prototype, 'webdriver', {{
    get: () => undefined, configurable: true
  }});

  if (!window.chrome) {{
    window.chrome = {{
      runtime: {{}},
      loadTimes: function () {{}},
      csi: function () {{}},
      app: {{ isInstalled: false }},
    }};
  }}

  const fakePlugins = [
    {{ name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' }},
    {{ name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: '' }},
    {{ name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: '' }},
    {{ name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: '' }},
    {{ name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: '' }},
  ];
  Object.defineProperty(Navigator.prototype, 'plugins', {{
    get: () => {{
      const arr = fakePlugins.slice();
      arr.item = (i) => arr[i] || null;
      arr.namedItem = (n) => arr.find(p => p.name === n) || null;
      return arr;
    }}, configurable: true
  }});

  Object.defineProperty(Navigator.prototype, 'languages', {{
    get: () => {languages_js}, configurable: true
  }});
  Object.defineProperty(Navigator.prototype, 'platform', {{
    get: () => {platform_js}, configurable: true
  }});
  Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', {{
    get: () => {fp.hardware_concurrency}, configurable: true
  }});
  Object.defineProperty(Navigator.prototype, 'deviceMemory', {{
    get: () => {fp.device_memory}, configurable: true
  }});

  const origQuery = (navigator.permissions && navigator.permissions.query)
    ? navigator.permissions.query.bind(navigator.permissions) : null;
  if (origQuery) {{
    navigator.permissions.query = (p) => (
      p && p.name === 'notifications'
        ? Promise.resolve({{ state: Notification.permission, onchange: null }})
        : origQuery(p)
    );
  }}

  const getParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function (param) {{
    if (param === 37445) return {webgl_vendor_js};
    if (param === 37446) return {webgl_renderer_js};
    return getParameter.call(this, param);
  }};
  if (window.WebGL2RenderingContext) {{
    const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function (param) {{
      if (param === 37445) return {webgl_vendor_js};
      if (param === 37446) return {webgl_renderer_js};
      return getParameter2.call(this, param);
    }};
  }}

  // Canvas fingerprint: stable 1-bit noise per session, invisible to the eye
  // but enough to break static-hash fingerprinting.
  const toBlob = HTMLCanvasElement.prototype.toBlob;
  const toDataURL = HTMLCanvasElement.prototype.toDataURL;
  const noisify = (canvas) => {{
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const w = canvas.width, h = canvas.height;
    if (w === 0 || h === 0) return;
    try {{
      const img = ctx.getImageData(0, 0, w, h);
      for (let i = 0; i < img.data.length; i += 4) {{
        img.data[i]     ^= 1;
        img.data[i + 1] ^= 1;
        img.data[i + 2] ^= 1;
      }}
      ctx.putImageData(img, 0, 0);
    }} catch (e) {{ /* tainted canvas — leave alone */ }}
  }};
  HTMLCanvasElement.prototype.toDataURL = function (...a) {{ noisify(this); return toDataURL.apply(this, a); }};
  HTMLCanvasElement.prototype.toBlob    = function (...a) {{ noisify(this); return toBlob.apply(this, a); }};

  Object.defineProperty(Screen.prototype, 'width',       {{ get: () => {sw}, configurable: true }});
  Object.defineProperty(Screen.prototype, 'height',      {{ get: () => {sh}, configurable: true }});
  Object.defineProperty(Screen.prototype, 'availWidth',  {{ get: () => {sw}, configurable: true }});
  Object.defineProperty(Screen.prototype, 'availHeight', {{ get: () => {sh - 40}, configurable: true }});
  Object.defineProperty(Screen.prototype, 'colorDepth',  {{ get: () => 24, configurable: true }});
  Object.defineProperty(Screen.prototype, 'pixelDepth',  {{ get: () => 24, configurable: true }});

  if (/HeadlessChrome/.test(navigator.userAgent)) {{
    Object.defineProperty(Navigator.prototype, 'userAgent', {{
      get: () => navigator.userAgent.replace(/HeadlessChrome/, 'Chrome'),
      configurable: true,
    }});
  }}
}})();
"""
