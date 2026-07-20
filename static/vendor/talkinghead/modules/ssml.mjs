/**
* MIT License
*
* Copyright (c) 2024 Mika Suominen
*
* Permission is hereby granted, free of charge, to any person obtaining a copy
* of this software and associated documentation files (the "Software"), to deal
* in the Software without restriction, including without limitation the rights
* to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
* copies of the Software, and to permit persons to whom the Software is
* furnished to do so, subject to the following conditions:
*
* The above copyright notice and this permission notice shall be included in all
* copies or substantial portions of the Software.
*
* THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
* IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
* FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
* AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
* LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
* OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
* SOFTWARE.
*/

class SSML {

  constructor( opt = null ) {
    this.opt = Object.assign({
      engine: "google"
    }, opt || {});

    this.reset();
  }

  reset() {
    this.root = { type: 'root', value: '', parent: null, children: [] };
    this.ssml = '';
  }

  traverse(node) {
    if (node.children) {
      if (node.type !== 'root') {
        this.ssml += `<${node.type}${node.value}>`;
      }
      node.children.forEach(x => {
        this.traverse(x);
      });
      this.ssml += `</${node.type}>`;
    } else {
      if (node.type === 'TEXT') {
        this.ssml += node.value;
      } else {
        this.ssml += `<${node.type}${node.value}/>`;
      }
    }
  }

  convert(s) {

    // Reset
    this.reset();

    // Sanitize
    s = s
    .replace(/\r?\n|\r/g, '') // remove new lines
    .replace(/>\s+</g, '><') // remove spaces between <tags>'s
    .replace(/<p>\s+<\/p>/g, '') // Remove empty <p> </p>
    .replace(/<p><\/p>/g, '') // Remove empty <p></p>
    .replace(/<s>\s+<\/s>/g, '') // Remove empty <s> </s>
    .replace(/<s><\/s>/g, '') // Remove empty <s></s>
    .replace(/<s\/>/g, '') // Remove self-closing <s/>
    .replace(/<p\/>/g, '') // Remove self-closing <p/>
    .trim();

    let text = '';
    let isText = false;
    let node = this.root;

    for (let i = 0, l = s.length; i < l; i++) {
      // SSML tag
      if (s[i] === '<') {

        // check if the text was already started - finish it and add to the parentNode
        if (isText) {
          isText = false;

          const newNode = {
            type: 'TEXT',
            value: text,
            parent: node,
            children: []
          };
          node.children.push(newNode);
        }

        // type and value/attributes of parsed SSML tag
        let type = '';
        let value = ''; // can be blank, like <tag /> or <tag></tag>

        let isEndTag = false; // flag for end tag (</tag>)
        let isEmptyTag = false; // flag for empty tag (<tag />)

        // start from next char
        let j = i + 1;

        // check if it is an end tag (no value)
        if (s[j] === '/') {
          isEndTag = true;

          // start from the next char
          j++;

          // parse only type
          while (s[j] !== '>') {
            type += s[j];
            j++;
          }
        } else {
          /*
           *  1. Parse type unless:
           *  ' ' - value is coming
           *  '>' - is start tag marker
           *  '/' - is empty tag marker
           */
          while (s[j] !== ' ' && s[j] !== '>' && s[j] !== '/') {
            type += s[j];
            j++;
          }

          // 2. Parse value
          while (true) {
            if (s[j] !== '>') {
              // A. value continues -> accumulate value
              value += s[j];
            } else if (s[j - 1] === '/') {
              // B. empty tag <tag />
              isEmptyTag = true;

              // remove last `/` char from value
              if (value.length !== 0) { value = value.slice(0, value.length - 1); }
              break;
            } else {
              // C. end tag </tag>
              break;
            }
            j++;
          }
        }

        /*
         * Process parsed results
         */

        if (!isEndTag) {
          const newNode = {
            parentNode: node,
            type: type,
            value: value,
            children: []
          };
          node.children.push(newNode);

          if (!isEmptyTag) {
            // Not an empty tag => can have other children, then keep it active
            node = newNode;
          }
        } else {
          // End tag
          node = node.parent;

          if ( node.type !== type ) {
            console.error("Incorrent SSML.")
          }
        }

        // skip processed chars for the next iteration
        i = j;
      } else {
        // Text

        if (!isText) {
          isText = true;
          text = '';
        }

        // accumulate characters
        text += s[i];

        if (i === l - 1 && isText) {
          // ssml ends with plain text => create node
          const newNode = {
            type: 'TEXT',
            value: text,
            parent: node,
            children: []
          };
          node.parent.push(newNode);
        }
      }
    }

    this.traverse(this.root);
    return this.ssml;
  }

}

export { SSML };
