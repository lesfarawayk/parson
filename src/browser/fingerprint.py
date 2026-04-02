"""Browser fingerprint generator — randomizes browser identity per context."""

import random


# Chrome versions with matching WebGL/platform data
_CHROME_VERSIONS = [
    {"major": 120, "full": "120.0.6099.130", "webkit": "537.36"},
    {"major": 121, "full": "121.0.6167.85", "webkit": "537.36"},
    {"major": 122, "full": "122.0.6261.94", "webkit": "537.36"},
    {"major": 123, "full": "123.0.6312.86", "webkit": "537.36"},
    {"major": 124, "full": "124.0.6367.91", "webkit": "537.36"},
    {"major": 125, "full": "125.0.6422.60", "webkit": "537.36"},
]

_WINDOWS_VERSIONS = [
    "Windows NT 10.0; Win64; x64",
    "Windows NT 10.0; WOW64",
    "Windows NT 11.0; Win64; x64",
]

_SCREEN_RESOLUTIONS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720},
    {"width": 1600, "height": 900},
    {"width": 2560, "height": 1440},
    {"width": 1280, "height": 800},
    {"width": 1680, "height": 1050},
]

_WEBGL_VENDORS = [
    "Google Inc. (NVIDIA)",
    "Google Inc. (AMD)",
    "Google Inc. (Intel)",
]

_WEBGL_RENDERERS = [
    "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (NVIDIA, NVIDIA GeForce RTX 2070 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (AMD, AMD Radeon RX 5700 XT Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "ANGLE (NVIDIA, NVIDIA GeForce GTX 1080 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)",
]

_LOCALES = ["ru-RU", "ru", "en-US", "en-GB"]

_TIMEZONES = [
    "Europe/Moscow",
    "Europe/Samara",
    "Europe/Volgograd",
    "Asia/Yekaterinburg",
    "Europe/Kaliningrad",
]

_PLATFORM_VARIANTS = ["Win32", "Win64"]

_HARDWARE_CONCURRENCY = [4, 6, 8, 12, 16]

_DEVICE_MEMORY = [4, 8, 16, 32]

_MAX_TOUCH_POINTS = [0, 0, 0, 0, 1, 5, 10]  # mostly 0 for desktop


def generate_fingerprint() -> dict:
    """
    Generate a random but consistent browser fingerprint.
    Returns a dict with all parameters needed for context creation and JS injection.
    """
    chrome = random.choice(_CHROME_VERSIONS)
    win = random.choice(_WINDOWS_VERSIONS)
    screen = random.choice(_SCREEN_RESOLUTIONS)
    webgl_vendor = random.choice(_WEBGL_VENDORS)
    webgl_renderer = random.choice(_WEBGL_RENDERERS)

    # Match vendor to renderer
    if "NVIDIA" in webgl_renderer:
        webgl_vendor = "Google Inc. (NVIDIA)"
    elif "AMD" in webgl_renderer:
        webgl_vendor = "Google Inc. (AMD)"
    elif "Intel" in webgl_renderer:
        webgl_vendor = "Google Inc. (Intel)"

    viewport_width = screen["width"] - random.randint(0, 120)
    viewport_height = screen["height"] - random.randint(60, 140)

    user_agent = (
        f"Mozilla/5.0 ({win}) "
        f"AppleWebKit/{chrome['webkit']} (KHTML, like Gecko) "
        f"Chrome/{chrome['full']} Safari/{chrome['webkit']}"
    )

    platform = random.choice(_PLATFORM_VARIANTS)
    hw_concurrency = random.choice(_HARDWARE_CONCURRENCY)
    device_memory = random.choice(_DEVICE_MEMORY)
    max_touch = random.choice(_MAX_TOUCH_POINTS)
    locale = random.choice(_LOCALES[:2])  # prefer Russian
    timezone = random.choice(_TIMEZONES)

    # Canvas noise seed — unique per fingerprint
    canvas_seed = random.randint(1, 1000000)

    return {
        "user_agent": user_agent,
        "viewport": {"width": viewport_width, "height": viewport_height},
        "screen": screen,
        "locale": locale,
        "timezone_id": timezone,
        "platform": platform,
        "hardware_concurrency": hw_concurrency,
        "device_memory": device_memory,
        "max_touch_points": max_touch,
        "webgl_vendor": webgl_vendor,
        "webgl_renderer": webgl_renderer,
        "chrome_version": chrome,
        "canvas_seed": canvas_seed,
    }


def build_stealth_script(fp: dict) -> str:
    """
    Build a JS init script that overrides browser properties to match the fingerprint.
    """
    return """
    (() => {
        // --- Navigator overrides ---
        const fp = """ + _fp_to_js(fp) + """;

        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        Object.defineProperty(navigator, 'platform', { get: () => fp.platform });
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => fp.hardwareConcurrency });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => fp.deviceMemory });
        Object.defineProperty(navigator, 'maxTouchPoints', { get: () => fp.maxTouchPoints });
        Object.defineProperty(navigator, 'languages', { get: () => [fp.locale, 'en-US', 'en'] });
        Object.defineProperty(navigator, 'language', { get: () => fp.locale });

        // --- Screen overrides ---
        Object.defineProperty(screen, 'width', { get: () => fp.screen.width });
        Object.defineProperty(screen, 'height', { get: () => fp.screen.height });
        Object.defineProperty(screen, 'availWidth', { get: () => fp.screen.width });
        Object.defineProperty(screen, 'availHeight', { get: () => fp.screen.height - 40 });
        Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
        Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });

        // --- WebGL fingerprint ---
        const getParameterOrig = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(param) {
            if (param === 0x9245) return fp.webglVendor;    // UNMASKED_VENDOR_WEBGL
            if (param === 0x9246) return fp.webglRenderer;  // UNMASKED_RENDERER_WEBGL
            return getParameterOrig.call(this, param);
        };
        const getParameterOrig2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(param) {
            if (param === 0x9245) return fp.webglVendor;
            if (param === 0x9246) return fp.webglRenderer;
            return getParameterOrig2.call(this, param);
        };

        // --- Canvas fingerprint noise ---
        const toDataURLOrig = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {
            const ctx = this.getContext('2d');
            if (ctx) {
                const imageData = ctx.getImageData(0, 0, this.width, this.height);
                const data = imageData.data;
                // Add subtle noise based on seed
                let seed = fp.canvasSeed;
                for (let i = 0; i < data.length; i += 4) {
                    seed = (seed * 16807 + 0) % 2147483647;
                    data[i] = data[i] ^ (seed & 1);     // R
                }
                ctx.putImageData(imageData, 0, 0);
            }
            return toDataURLOrig.call(this, type);
        };

        // --- AudioContext fingerprint ---
        const origGetFloatFreq = AnalyserNode.prototype.getFloatFrequencyData;
        AnalyserNode.prototype.getFloatFrequencyData = function(array) {
            origGetFloatFreq.call(this, array);
            let seed = fp.canvasSeed;
            for (let i = 0; i < array.length; i++) {
                seed = (seed * 16807 + 0) % 2147483647;
                array[i] += (seed % 100) * 0.00001;
            }
        };

        // --- Plugins (fake realistic set) ---
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const arr = [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin' },
                ];
                arr.length = 3;
                return arr;
            }
        });

        // --- Permissions API ---
        const origQuery = Permissions.prototype.query;
        Permissions.prototype.query = function(params) {
            if (params.name === 'notifications') {
                return Promise.resolve({ state: Notification.permission });
            }
            return origQuery.call(this, params);
        };

        // --- Chrome runtime stub ---
        if (!window.chrome) window.chrome = {};
        if (!window.chrome.runtime) window.chrome.runtime = {};

    })();
    """


def _fp_to_js(fp: dict) -> str:
    """Convert fingerprint dict to JS object literal."""
    return (
        "{"
        f"platform:'{fp['platform']}',"
        f"hardwareConcurrency:{fp['hardware_concurrency']},"
        f"deviceMemory:{fp['device_memory']},"
        f"maxTouchPoints:{fp['max_touch_points']},"
        f"locale:'{fp['locale']}',"
        f"screen:{{width:{fp['screen']['width']},height:{fp['screen']['height']}}},"
        f"webglVendor:'{fp['webgl_vendor']}',"
        f"webglRenderer:'{_escape_js(fp['webgl_renderer'])}',"
        f"canvasSeed:{fp['canvas_seed']}"
        "}"
    )


def _escape_js(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'").replace("(", "\\(").replace(")", "\\)")
