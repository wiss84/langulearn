
// Default styles
const BORDER_RADIUS = "10px";
const FONT = "13px Helvetica";
const COLOR_1 = "#ffffff";
const COLOR_2 = "#c0c0c0";
const COLOR_3 = "#606060";
const COLOR_4 = "#202020";
const LANE_COLOR_1 = "#808080";
const LANE_COLOR_2 = "#c0c0c0";

// Tempate for custom theme
/*
:root {
  --ui-headaudio-border-radius: 10px;
  --ui-headaudio-font: '13px Helvetica';
  --ui-headaudio-color-1: #ffffff;
  --ui-headaudio-color-2: #c0c0c0c0;
  --ui-headaudio-color-3: #606060;
  --ui-headaudio-color-4: #202020;
  --ui-headaudio-color-lane-1: #808080;
  --ui-headaudio-color-lane-2: #c0c0c0;
}
*/

// Constants
const VISEMES_ROWS = 13;
const VISEMES_COLS = 150;
const VISEMES_CUTS = [ -0.5, 0.5 ];
const VISEMES_COLORS = [ "#2020e0", "#808080", "#e02020" ];
const VISEMES_COLOR_BACKGROUND = "#202020";
const VISEMES_FONT = "20px \"Trebuchet MS\"";

const VAD_COLOR_BACKGROUND = "#202020";
const VAD_COLOR_VALLEY= "#606060";
const VAD_COLOR_INACTIVE= "#c0c0c0";
const VAD_COLOR_ACTIVE = "#ffffff";
const VAD_COLOR_LIGHT_OFF = "#c06060";
const VAD_COLOR_LIGHT_ON = "#60c060";
const VAD_FALLSPEED = 0.96; // base decay factor (closer to 1 = slower)
const VAD_FALLMIN = 0.2; // minimum drop speed in dB per frame

class Visemes extends HTMLElement {
  
  constructor() {
    super();

    // Shadow
    const shadow = this.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = `
      :host {
        display: block; width: 100%; height: 100%;
        min-height: 0; min-width: 0; overflow: hidden;
      }
      canvas {
        width: 100%; height: 100%; display: block;
        border-radius: var(--ui-headaudio-border-radius, ${BORDER_RADIUS});
      }
    `;
    const canvas = document.createElement('canvas');
    canvas.width = 10 * VISEMES_COLS;
    canvas.height = 300 + 20;
    shadow.append(style, canvas);
    this.canvas = canvas;
    this.ctx = this.canvas.getContext('2d');

  }

  connectedCallback() {
    this.clear();
  }

  disconnectedCallback() {
  }

  clear() {
    const ctx = this.ctx;
    const { width, height } = this.canvas;
    ctx.fillStyle = VISEMES_COLOR_BACKGROUND;
    ctx.fillRect(0, 0, width, height);
  }

  valueToColor(v) {
    const N = VISEMES_CUTS.length;
    let color = VISEMES_COLORS[N];
    for( let i=0; i<N; i++ ) {
      if ( v < VISEMES_CUTS[i] ) {
        color = VISEMES_COLORS[i];
        break;
      }
    }
    return color;
  }

  /**
  * Adds n empty columns (cleared space) to the right side of the canvas.
  * @param {number} n - Number of columns to add (default 2)
  */
  addBreak(n = 2) {
    const ctx = this.ctx;
    const { width, height } = this.canvas;
    const cellW = width / VISEMES_COLS;
    const shift = n * cellW;
    
    // Shift the current image left by n columns
    ctx.drawImage(this.canvas,
      shift, 0, width - shift, height,  // source rect
      0, 0, width - shift, height       // destination rect
    );

    // Fill the new right-side area with background color
    ctx.fillStyle = VISEMES_COLOR_BACKGROUND;
    ctx.fillRect(width - shift, 0, shift, height);
  }

  write(s) {
    const ctx = this.ctx;
    const { width, height } = this.canvas;

    ctx.font = VISEMES_FONT;
    ctx.textAlign = "right";
    ctx.textBaseline = "top";
    ctx.fillStyle = "white";

    ctx.fillText(s, width - 2, 3 );
  }

  add(f, factor=1) {
    const ctx = this.ctx;
    const { width, height } = this.canvas;
    const cellW = factor * (width / VISEMES_COLS);
    const cellH = (height - 20) / VISEMES_ROWS;

    // Shift entire canvas left by one column
    ctx.drawImage(
      this.canvas,
      cellW, 0, width - cellW, height, // source rect
      0, 0, width - cellW, height      // destination rect
    );

    // Clear last column
    ctx.fillStyle = VISEMES_COLOR_BACKGROUND;
    ctx.fillRect( width - cellW, 0, cellW, height );

    // Draw new column on the right side
    if ( f ) {
      const x = width - cellW;
      let y = 0;
      for (let i = 0; i < 12; i++) {
        ctx.fillStyle = this.valueToColor(f[i]);
        ctx.fillRect(x, i * cellH + y + cellH / 2 + 20, cellW - 1, cellH - 1);
      }
      /* y += cellH;
      for (let i = 12; i <= 23; i++) {
        ctx.fillStyle = this.valueToColor(f[i]);
        ctx.fillRect(x, i * cellH + y + cellH / 2 + 20, cellW - 1, cellH - 1);
      } */
    }
  }

}

class Events extends HTMLElement {

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          width: 100%; height: 100%; position: relative; overflow: hidden;
        }
        .container {
          border: 0; width: 100%; height: 100%;
          overflow-y: scroll; overflow-x: hidden; scrollbar-gutter: stable;
        }
        @supports (scrollbar-color: auto) {
          .container {
            scrollbar-color: var(--ui-events-color-3, ${COLOR_3}) var(--ui-events-color-3, ${COLOR_2});
          }
        }
        @supports selector(::-webkit-scrollbar) {
          .container::-webkit-scrollbar {
            background: var(--ui-events-color-2, ${COLOR_2});
          }
          .container::-webkit-scrollbar-thumb {
            background: var(--ui-events-color-3, ${COLOR_3});
          }
        }
        table { width: 100%; border-collapse: collapse; table-layout: auto; }
        td {
          padding: 2px 4px; word-wrap: break-word; white-space: pre-wrap;
          vertical-align: top; font-family: monospace;
          color: var(--ui-headaudio-color-4, ${COLOR_4});
          font: var(--ui-headaudio-font, ${FONT});
        }
        td:first-child { white-space: nowrap; font-weight: bold; }
        tr:nth-child(odd) { background: var(--ui-headaudio-color-lane-1, ${LANE_COLOR_1}); }
        tr:nth-child(even) { background: var(--ui-headaudio-color-lane-2, ${LANE_COLOR_2}); }
      </style>
      <div class="container">
        <table>
          <colgroup>
            <col /><col /><col />
          </colgroup>
          <tbody></tbody>
        </table>
      </div>
    `;
    this._tbody = this.shadowRoot.querySelector('tbody');
  }

  addEvent(event, text = "", clip = null) {
    const tr = document.createElement('tr');
    const td1 = document.createElement('td');
    const td2 = document.createElement('td');
    const td3 = document.createElement('td');
    td1.textContent = event;
    td2.textContent = text;
    if ( clip ) {
      const a = document.createElement("a");
      a.href = "#";
      a.textContent = "[Copy]";
      a.addEventListener("click", function(event) {
        event.preventDefault(); // prevent navigation
        navigator.clipboard.writeText(clip);
      });
      td3.appendChild(a);
    } else {
      td3.textContent="";
    }
    tr.appendChild(td1);
    tr.appendChild(td2);
    tr.appendChild(td3);
    this._tbody.appendChild(tr);

    // auto-scroll to bottom
    this.shadowRoot.querySelector('.container').scrollTop =
    this.shadowRoot.querySelector('.container').scrollHeight;
  }

  addViseme(o) {
    this.addEvent("VISEME",o.viseme,o.vector);
  }

  clear() {
    this._tbody.innerHTML = '';
  }
}

class Vad extends HTMLElement {
  
  constructor() {
    super();

    // Limits and values
    this._db = -100;
    this._active = 0;
    this._dbactive = -40;
    this._dbinactive = -50;
    this._top = -100;
    this._last = -100;

    // Pre-render leds
    this._ledOnCanvas = this._createLed(true);
    this._ledOffCanvas = this._createLed(false);

    // Shadow
    const shadow = this.attachShadow({ mode: 'open' });
    const canvas = document.createElement('canvas');
    canvas.width = 40;
    canvas.height = 280;
    const style = document.createElement('style');
    style.textContent = `
      :host { display: block; height: 100%; }
      canvas {
        display: block; height: 100%; width: auto;
        border-radius: var(--ui-headaudio-border-radius, ${BORDER_RADIUS});
      }
    `;
    shadow.append(style, canvas);
    this.canvas = canvas;
    this.ctx = this.canvas.getContext('2d');

  }

  connectedCallback() {
    this.update();
  }

  setActive(x) {
    this._dbactive = x;
    this.update();
  }

  setInactive(x) {
    this._dbinactive = x;
    this.update();
  }

  update(vad=null) {
    this._db = vad ? vad.db : this._db;
    this._active = vad ? vad.active : this._active;

    const ctx = this.ctx;
    const { width, height } = this.canvas;

    this._last = 0.75 * this._last + 0.25 * this._db;

    // Update top
    if (this._db > this._top) {
      this._top = this._db;
    } else {
      // Exponential decay + guaranteed minimum fall
      const diff = this._top - this._db;
      const decay = diff * (1 - VAD_FALLSPEED);  
      const drop = Math.max(decay, VAD_FALLMIN);
      this._top -= drop;
    }
    
    const yBlock = -3 * this._last + 60;
    const hBlock = height - yBlock;
    const yValley = -3 * this._dbactive + 60;
    const hValley = 3 * (this._dbactive - this._dbinactive);
    const yTop = -3 * this._top + 60;
    const hTop = 3 * ( this._top - this._last );

    // Clear
    ctx.fillStyle = VAD_COLOR_BACKGROUND;
    ctx.fillRect(0, 0, width, height);

    // Light
    this.ctx.drawImage(
        this._active ? this._ledOnCanvas : this._ledOffCanvas,
        0, 0, 40, 40
    );

    // Draw valley
    if ( hValley > 0 ) { 
      ctx.fillStyle = VAD_COLOR_VALLEY;
      ctx.fillRect( 0, yValley, width, hValley );
    }

    // Draw top
    if ( hTop > 0 ) {
      ctx.fillStyle = VAD_COLOR_ACTIVE;
      ctx.fillRect( 15, yTop , 10, hTop );
    }

    // Draw volume
    ctx.fillStyle = VAD_COLOR_INACTIVE;
    ctx.fillRect( 15, yBlock , 10, hBlock );

  }

  _createLed(isOn) {
    const off = document.createElement("canvas");
    off.width = 40;
    off.height = 40;
    const octx = off.getContext("2d");

    if (isOn) {
        // Glow pass
        const glow = document.createElement("canvas");
        glow.width = 40;
        glow.height = 40;
        const gctx = glow.getContext("2d");

        gctx.fillStyle = VAD_COLOR_LIGHT_ON;
        gctx.beginPath();
        gctx.arc(20, 20, 12, 0, Math.PI * 2);
        gctx.fill();

        octx.filter = "blur(22px)";
        octx.globalCompositeOperation = "lighter";
        octx.drawImage(glow, 0, 0);

        octx.filter = "none";
        octx.globalCompositeOperation = "source-over";

        // LED top layer
        octx.fillStyle = VAD_COLOR_LIGHT_ON;
    } else {
        // off LED color (dim)
        octx.fillStyle = VAD_COLOR_LIGHT_OFF;
    }

    octx.beginPath();
    octx.arc(20, 20, 12, 0, Math.PI * 2);
    octx.fill();

    return off;
  }

}

customElements.define('ui-headaudio-vad', Vad);
customElements.define('ui-headaudio-visemes', Visemes);
customElements.define('ui-headaudio-events', Events);
