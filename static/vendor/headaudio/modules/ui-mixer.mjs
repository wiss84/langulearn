

// Default styles
const MARGIN = "5px";
const BACKGROUND_COLOR = "transparent";
const FONT = "13px Helvetica";
const FONT_VALUE = "16px Helvetica";
const COLOR_1 = "#ffffff";
const COLOR_2 = "#c0c0c0";
const COLOR_3 = "#606060";
const COLOR_4 = "#202020";
const HEADER_WIDTH = "13px";
const SEPARATOR_WIDTH = "16px";
const RANGE_WIDTH = "30px";
const BUTTON_WIDTH = "68px";
const ONOFF_WIDTH = "68px";

// Tempate for custom theme
/*
:root {
  --ui-mixer-margin: 5px;
  --ui-mixer-background-color: transparent;
  --ui-mixer-font: '13px Helvetica';
  --ui-mixer-font-value: '16px Helvetica';
  --ui-mixer-color-1: #ffffff;
  --ui-mixer-color-2: #c0c0c0c0;
  --ui-mixer-color-3: #606060;
  --ui-mixer-color-4: #202020;
  --ui-mixer-header-width: 18px;
  --ui-mixer-separator-width: 16px;
  --ui-mixer-range-width: 36px;
  --ui-mixer-button-width: 68px;
  --ui-mixer-onoff-width: 68px;
}
*/

// Styles
const HOST = `:host {
  margin: var(--ui-mixer-margin, ${MARGIN}); padding: 0;
  background-color: var(--ui-mixer-background-color, ${BACKGROUND_COLOR});
  display: flex; position: relative;
}`;
const EXTRAS = `.noselect {
    -webkit-touch-callout: none; -webkit-user-select: none;
    -khtml-user-select: none; -moz-user-select: none; -ms-user-select: none;
    user-select: none; -webkit-user-select: none;
  }
  .nodrag {
    -webkit-user-drag: none; -khtml-user-drag: none; -moz-user-drag: none;
    -o-user-drag: none; -ms-user-drag: none; user-drag: none;
}`;

// Other constants
const THUMB_HEIGHT = 15;

// HEADER
class Header extends HTMLElement {

  constructor() {
    super();

    // Shadow
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.innerHTML = `
      <style>
        ${HOST}
        .container {
          width: var(--ui-mixer-header-width, ${HEADER_WIDTH});
          display: flex; justify-content: center; align-items: center;
        }
        .label {
          transform: rotate(-90deg);
          transform-origin: center;
          font: var(--ui-mixer-font, ${FONT});
          color: var(--ui-mixer-color-2, ${COLOR_2});
          text-wrap: nowrap;
        }
        ${EXTRAS}
      </style>
      <div class="container noselect nodrag">
        <div class="label">
          <slot></slot>
        </div>
      </div>
    `;

  }

}

// SEPARATOR
class Separator extends HTMLElement {
  
  constructor() {
    super();

    // Shadow
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.innerHTML = `
      <style>
        ${HOST}
        .container {
          width: var(--ui-mixer-separator-width, ${SEPARATOR_WIDTH});
          display: flex; justify-content: center; align-items: center; 
        }
        .divider {
          height: 100%;
          border-left: 2px solid var(--ui-mixer-color-3, ${COLOR_3});
        }
        ${EXTRAS}
      </style>
      <div class="container noselect nodrag">
        <div class="divider"></div>
      </div>
    `;
  }

}

// ONOFF
class OnOff extends HTMLElement {
  static formAssociated = true;

  constructor() {
    super();
    this._internals = this.attachInternals();

    // Shadow
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.innerHTML = `
      <style>
        ${HOST}
        .container {
          width: var(--ui-mixer-onoff-width, ${ONOFF_WIDTH});
          display: flex; justify-content: center; align-items: center;
        }
        input {
          position: absolute; opacity: 0; pointer-events: none;
        }
        .onoff {
          width: var(--ui-mixer-onoff-width, ${ONOFF_WIDTH});
          height: Calc( 2 * var(--ui-mixer-onoff-width, ${ONOFF_WIDTH}) / 5);
          border: 0; border-radius: 6px;
          background-color: var(--ui-mixer-color-3, ${COLOR_3});
          font: var(--ui-mixer-font, ${FONT});
          color: var(--ui-mixer-color-4, ${COLOR_4});
          text-wrap: nowrap;
          display: inline-flex; align-items: center; justify-content: center;
          cursor: pointer;
          box-shadow: 0px 1px 2px 0px rgba(60, 64, 67, 0.3), 0px 1px 3px 1px rgba(60, 64, 67, 0.15);
        }
        :host([checked]) .onoff {
          background: radial-gradient(circle at 30% 30%, var(--ui-mixer-color-1, ${COLOR_1}), var(--ui-mixer-color-2, ${COLOR_2}));
          box-shadow: 0 0 7px 1px var(--ui-mixer-color-light, ${COLOR_2});
        }
        ${EXTRAS}
      </style>
      <div class="container noselect nodrag">
        <input type="checkbox">
        <div class="onoff">
          <slot></slot>
        </div>
      </div>
    `;

    this._input = this.shadowRoot.querySelector("input");
    this._onoff = this.shadowRoot.querySelector(".onoff");

  }

  connectedCallback() {
    this._input.checked = this.hasAttribute("checked");
    this._sync();
    this._onoff.addEventListener("click", this._onClick.bind(this));
  }

  disconnectedCallback() {
    this._onoff.removeEventListener("click", this._onClick);
  }

  _onClick(e) {
    this._input.checked = !this._input.checked;
    this._sync();
    this.dispatchEvent(
      new CustomEvent("ui-mixer-click", {
        detail: { checked: this._input.checked, originalEvent: e },
        bubbles: true,
        composed: true
      })
    );
  }

  _sync() {
    if (this._input.checked) {
      this.setAttribute("checked", "");
    } else {
      this.removeAttribute("checked");
    }
  }

  get checked() {
    return this._input.checked;
  }
  set checked(val) {
    this._input.checked = Boolean(val);
    this._sync();
  }

  set disabled(val) {
    if (val) {
      this.setAttribute("disabled", "");
    } else {
      this.removeAttribute("disabled");
    }
  }

  get disabled() {
    return this.hasAttribute("disabled");
  }

}

// Button
class Button extends HTMLElement {
  static formAssociated = true;

  constructor() {
    super();
    this._internals = this.attachInternals();

    // Shadow
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.innerHTML = `
      <style>
        ${HOST}
        .container {
          width: var(--ui-mixer-onoff-width, ${BUTTON_WIDTH});
          display: flex; justify-content: center; align-items: center;
        }
        .button {
          width: var(--ui-mixer-onoff-width, ${BUTTON_WIDTH});
          height: Calc( 2 * var(--ui-mixer-onoff-width, ${BUTTON_WIDTH}) / 5);
          border: 2px solid var(--ui-mixer-color-3, ${COLOR_3});
          border-radius: 6px;
          background-color: var(--ui-mixer-color-2, ${COLOR_2});
          font: var(--ui-mixer-font, ${FONT});
          color: var(--ui-mixer-color-4, ${COLOR_4});
          text-wrap: nowrap;
          font-weight: 500;
          display: inline-flex; align-items: center; justify-content: center;
          cursor: pointer;
          box-shadow: 0px 1px 2px 0px rgba(60, 64, 67, 0.3), 0px 1px 3px 1px rgba(60, 64, 67, 0.15);
        }
        .button:active {
          background-color: var(--ui-mixer-color-1, ${COLOR_1});
          transform: scale(0.97);
        }
        ${EXTRAS}
      </style>
      <div class="container noselect nodrag">
        <div class="button">
          <slot></slot>
        </div>
      </div>
    `;

    this._button = this.shadowRoot.querySelector(".button");

  }

  connectedCallback() {
    this._button.addEventListener("click", this._onClick.bind(this));
  }

  disconnectedCallback() {
    this._button.removeEventListener("click", this._onClick);
  }

  _onClick(e) {
    this.dispatchEvent(
      new CustomEvent("ui-mixer-click", {
        detail: { originalEvent: e },
        bubbles: true,
        composed: true
      })
    );
  }

  set disabled(val) {
    if (val) {
      this.setAttribute("disabled", "");
    } else {
      this.removeAttribute("disabled");
    }
  }

  get disabled() {
    return this.hasAttribute("disabled");
  }

}

class Range extends HTMLElement {
  static formAssociated = true;
  
  constructor() {
    super();
    this._internals = this.attachInternals();

    // Callbacks
    this._callbacks = new Set();

    // Shadow
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.innerHTML = `
      <style>
        ${HOST}
        .container {
          width: var(--ui-mixer-range-width, ${RANGE_WIDTH});
          position: relative;
        }
        .value-container {
          height: 30px;
          display: flex; justify-content: center; align-items: center;
        }
        .value {
          flex: 1;
          text-align: center;
          font: var(--ui-mixer-font-value, ${FONT_VALUE});
          color: var(--ui-mixer-color-1, ${COLOR_1});
          text-wrap: nowrap;
        }
        .label-container {
          height: 30px;
          display: flex; justify-content: center; align-items: center;
        }
        .label {
          flex: 1;
          text-align: center;
          font: var(--ui-mixer-font, ${FONT});
          color: var(--ui-mixer-color-4, ${COLOR_4});
          text-wrap: nowrap;
        }
        .range-container {
          height: Calc(100% - 60px);
          position: relative;
          width: 100%;
        }
        .range {
          writing-mode: vertical-lr;
          direction: rtl;
          width: 100%; height: 100%;
          -webkit-appearance: none; appearance: none;
          -moz-appearance: none;
          background: transparent;
          margin: 0; padding: 0;
          position: relative;
          z-index: 3;
        }
        input[type="range"]::-webkit-slider-thumb {
          -webkit-appearance: none;
          appearance: none;
          width: 0; height: 0;
          opacity: 0;
        }
        input[type="range"]::-moz-range-thumb {
          -moz-appearance: none;
          width: 0; height: 0;
          border: none;
          opacity: 0;
        }
        .track {
          position: absolute;
          inset: 0;
          margin: auto;
          width: 8px;
          background: var(--ui-mixer-color-2, ${COLOR_2});
          border-radius: 3px;
          z-index: 0;
        }
        .fill {
          position: absolute; left: 50%; bottom: 0;
          transform: translateX(-50%);
          width: 10px; height: 0;
          background: var(--ui-mixer-color-4, ${COLOR_4});
          border-radius: 3px;
          z-index: 1;
          pointer-events: none;
        }
        .thumb {
          position: absolute; left: 50%;
          width: 26px; height: ${THUMB_HEIGHT}px;
          transform: translateX(-50%);
          background: var(--ui-mixer-color-3, ${COLOR_3});
          border: 2px solid var(--ui-mixer-color-4, ${COLOR_4});
          border-radius: 4px;
          z-index: 2;
          pointer-events: none;
          display: flex; justify-content: center; align-items: center;
          color: var(--ui-mixer-color-4, ${COLOR_4});
          box-shadow: 0px 2px 4px 0px rgba(60, 64, 67, 0.4), 0px 2px 6px 2px rgba(60, 64, 67, 0.2);
        }
        ${EXTRAS}
      </style>
      <div class="container noselect nodrag">
        <div class="value-container">
          <div class="value"></div>
        </div>
        <div class="range-container">
          <div class="track"></div>
          <div class="fill"></div>
          <div class="thumb">&#9868;</div>
          <input type="range" class="range">
        </div>
        <div class="label-container">
          <div class="label">
            <slot></slot>
          </div>
        </div>
      </div>
    `;

    this._input = this.shadowRoot.querySelector("input");
    this._fill = this.shadowRoot.querySelector('.fill');
    this._thumb = this.shadowRoot.querySelector('.thumb');
    this._value = this.shadowRoot.querySelector(".value");
    this._onInput = this._onInput.bind(this);

  }

  connectedCallback() {
    this._sync();
    this._input.addEventListener("input", this._onInput.bind(this) );
    this._input.addEventListener("change", this._onChange.bind(this) );
    window.addEventListener("resize", this._onResize.bind(this) );
    

    // Live update
    this._input.addEventListener("input", (e) => {
      
    });

    // Final/committed value
    this._input.addEventListener("change", (e) => {
      
    });
  }

  disconnectedCallback() {
    window.removeEventListener("resize", this._onResize);
    this._input.removeEventListener("input", this._onInput);
  }

  attributeChangedCallback() {
    this._sync();
  }

  _onResize() {
    this._sync();
  }

  _onInput(e) {
    const v = this._input.value;
    this.setAttribute("value", v);
    this._value.textContent = v;

    this.dispatchEvent(
      new CustomEvent("ui-mixer-input", {
        detail: { value: this._input.value, originalEvent: e },
        bubbles: true,
        composed: true
      })
    );
  }

  _onChange(e) {
    const v = this._input.value;
    this.setAttribute("value", v);
    this._value.textContent = v;

    this.dispatchEvent(
      new CustomEvent("ui-mixer-change", {
        detail: { value: this._input.value, originalEvent: e },
        bubbles: true,
        composed: true
      })
    );
  }

  _sync() {
    // Reflect input-related attributes
    for (const attr of ["min", "max", "step", "value", "list"]) {
      if (this.hasAttribute(attr)) {
        this._input[attr] = this.getAttribute(attr);
      }
    }

    // Value display
    this._value.textContent = this._input.value;

    // Custom thumb and fill
    const val = +this._input.value;
    const min = +this._input.min;
    const max = +this._input.max;
    const percent = (val - min) / (max - min);
    const h = this._input.clientHeight;
    const thumbY = (1 - percent) * (h - THUMB_HEIGHT);
    this._fill.style.height = `${percent * h}px`;
    this._thumb.style.top = `${thumbY}px`;
  }

  static get observedAttributes() {
    return ["min", "max", "step", "value", "list"];
  }

  get value() { return this._input.value; }
  set value(x) { this.setAttribute("value", x); }

  set disabled(val) {
    if (val) {
      this.setAttribute("disabled", "");
    } else {
      this.removeAttribute("disabled");
    }
  }

  get disabled() {
    return this.hasAttribute("disabled");
  }

}

customElements.define('ui-mixer-header', Header);
customElements.define('ui-mixer-separator', Separator);
customElements.define('ui-mixer-onoff', OnOff);
customElements.define('ui-mixer-button', Button);
customElements.define('ui-mixer-range', Range);
