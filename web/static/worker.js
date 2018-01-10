//postMessage({status: "log", message: "got here 0"});
//importScripts(
//    "https://cdn.polyfill.io/v2/polyfill.js" +
//    "?features=Array.map,Object.keys,Promise,XMLHttpRequest,performance.now" +
//    "&flags=gated&unknown=polyfill&rum=0"
//);
// The code returned by the polyfill service isn't worker-safe.
// Starting here is what you get from the above URL with *extensive*
// manual corrections.  In particular, note that if you have workers
// at all, you have XMLHttpRequest (and if you don't have workers,
// this code is being run in the document context and XMLHttpRequest
// has already been polyfilled).  Which is nice, because the XHR
// polyfill is catastrophically non-worker-safe.

/* Polyfill service v3.12.1
 * For detailed credits and licence information see http://github.com/financial-times/polyfill-service.
 *
 * UA detected: curl/7.50.1 (unknown/unsupported; using policy `unknown=polyfill`)
 * Features requested: Array.map,Object.keys,Promise,XMLHttpRequest,performance.now
 *
 * - Object.keys, License: CC0
 * - setImmediate, License: CC0 (required by "Promise")
 * - Object.defineProperty, License: CC0 (required by "Array.isArray", "Event", "Promise", "XMLHttpRequest")
 * - Array.isArray, License: CC0 (required by "Promise")
 * - Promise, License: MIT
 * - Date.now, License: CC0 (required by "performance.now")
 * - performance.now, License: CC0 */

(function(undefined) {
// Object.keys
if (!('keys' in this.Object)) {
this.Object.keys = (function(Object) {
	'use strict';
	var hasOwnProperty = Object.prototype.hasOwnProperty,
	hasDontEnumBug = !({ toString: null }).propertyIsEnumerable('toString'),
	dontEnums = [
		'toString',
		'toLocaleString',
		'valueOf',
		'hasOwnProperty',
		'isPrototypeOf',
		'propertyIsEnumerable',
		'constructor'
	],
	dontEnumsLength = dontEnums.length;

	return function(obj) {
		if (typeof obj !== 'object' && (typeof obj !== 'function' || obj === null)) {
			throw new TypeError('Object.keys called on non-object');
		}

		var result = [], prop, i;

		for (prop in obj) {
			if (hasOwnProperty.call(obj, prop)) {
				result.push(prop);
			}
		}

		if (hasDontEnumBug) {
			for (i = 0; i < dontEnumsLength; i++) {
				if (hasOwnProperty.call(obj, dontEnums[i])) {
					result.push(dontEnums[i]);
				}
			}
		}
		return result;
	};
}(this.Object));
}

// setImmediate
if (!('setImmediate' in this)) {
(function (global, undefined) {
    "use strict";

    if (global.setImmediate) {
        return;
    }

    var nextHandle = 1; // Spec says greater than zero
    var tasksByHandle = {};
    var currentlyRunningATask = false;
    var doc = global.document;
    var setImmediate;

    function addFromSetImmediateArguments(args) {
        tasksByHandle[nextHandle] = partiallyApplied.apply(undefined, args);
        return nextHandle++;
    }

    // This function accepts the same arguments as setImmediate, but
    // returns a function that requires no arguments.
    function partiallyApplied(handler) {
        var args = [].slice.call(arguments, 1);
        return function() {
            if (typeof handler === "function") {
                handler.apply(undefined, args);
            } else {
                (new Function("" + handler))();
            }
        };
    }

    function runIfPresent(handle) {
        // From the spec: "Wait until any invocations of this algorithm started before this one have completed."
        // So if we're currently running a task, we'll need to delay this invocation.
        if (currentlyRunningATask) {
            // Delay by doing a setTimeout. setImmediate was tried instead, but in Firefox 7 it generated a
            // "too much recursion" error.
            global.setTimeout(partiallyApplied(runIfPresent, handle), 0);
        } else {
            var task = tasksByHandle[handle];
            if (task) {
                currentlyRunningATask = true;
                try {
                    task();
                } finally {
                    clearImmediate(handle);
                    currentlyRunningATask = false;
                }
            }
        }
    }

    function clearImmediate(handle) {
        delete tasksByHandle[handle];
    }

    function installNextTickImplementation() {
        setImmediate = function() {
            var handle = addFromSetImmediateArguments(arguments);
            global.process.nextTick(partiallyApplied(runIfPresent, handle));
            return handle;
        };
    }

    function canUsePostMessage() {
        // The test against `importScripts` prevents this implementation from being installed inside a web worker,
        // where `global.postMessage` means something completely different and can't be used for this purpose.
        if (global.postMessage && !global.importScripts) {
            var postMessageIsAsynchronous = true;
            var oldOnMessage = global.onmessage;
            global.onmessage = function() {
                postMessageIsAsynchronous = false;
            };
            global.postMessage("", "*");
            global.onmessage = oldOnMessage;
            return postMessageIsAsynchronous;
        }
        return false;
    }

    function installPostMessageImplementation() {
        // Installs an event handler on `global` for the `message` event: see
        // * https://developer.mozilla.org/en/DOM/window.postMessage
        // * http://www.whatwg.org/specs/web-apps/current-work/multipage/comms.html#crossDocumentMessages

        var messagePrefix = "setImmediate$" + Math.random() + "$";
        var onGlobalMessage = function(event) {
            if (event.source === global &&
                typeof event.data === "string" &&
                event.data.indexOf(messagePrefix) === 0) {
                runIfPresent(+event.data.slice(messagePrefix.length));
            }
        };

        if (global.addEventListener) {
            global.addEventListener("message", onGlobalMessage, false);
        } else {
            global.attachEvent("onmessage", onGlobalMessage);
        }

        setImmediate = function() {
            var handle = addFromSetImmediateArguments(arguments);
            global.postMessage(messagePrefix + handle, "*");
            return handle;
        };
    }

    function installMessageChannelImplementation() {
        var channel = new global.MessageChannel();
        channel.port1.onmessage = function(event) {
            var handle = event.data;
            runIfPresent(handle);
        };

        setImmediate = function() {
            var handle = addFromSetImmediateArguments(arguments);
            channel.port2.postMessage(handle);
            return handle;
        };
    }

    function installReadyStateChangeImplementation() {
        var html = doc.documentElement;
        setImmediate = function() {
            var handle = addFromSetImmediateArguments(arguments);
            // Create a <script> element; its readystatechange event will be fired asynchronously once it is inserted
            // into the document. Do so, thus queuing up the task. Remember to clean up once it's been called.
            var script = doc.createElement("script");
            script.onreadystatechange = function () {
                runIfPresent(handle);
                script.onreadystatechange = null;
                html.removeChild(script);
                script = null;
            };
            html.appendChild(script);
            return handle;
        };
    }

    function installSetTimeoutImplementation() {
        setImmediate = function() {
            var handle = addFromSetImmediateArguments(arguments);
            global.setTimeout(partiallyApplied(runIfPresent, handle), 0);
            return handle;
        };
    }

    // If supported, we should attach to the prototype of global, since that is where setTimeout et al. live.
    var attachTo = global.Object.getPrototypeOf && global.Object.getPrototypeOf(global);
    attachTo = attachTo && attachTo.setTimeout ? attachTo : global;

    // Don't get fooled by e.g. browserify environments.
    if ({}.toString.call(global.process) === "[object process]") {
        // For Node.js before 0.9
        installNextTickImplementation();

    } else if (canUsePostMessage()) {
        // For non-IE10 modern browsers
        installPostMessageImplementation();

    } else if (global.MessageChannel) {
        // For web workers, where supported
        installMessageChannelImplementation();

    } else if (doc && "onreadystatechange" in doc.createElement("script")) {
        // For IE 6–8
        installReadyStateChangeImplementation();

    } else {
        // For older browsers
        installSetTimeoutImplementation();
    }

    attachTo.setImmediate = setImmediate;
    attachTo.clearImmediate = clearImmediate;
}(this)); // eslint-disable-line no-undef

}

// Object.defineProperty
if (!(// In IE8, defineProperty could only act on DOM elements, so full support
// for the feature requires the ability to set a property on an arbitrary object
'defineProperty' in this.Object && (function() {
	try {
		var a = {};
		this.Object.defineProperty(a, 'test', {value:42});
		return true;
	} catch(e) {
		return false;
	}
}.call(this)))) {

(function (global) {

	var supportsAccessors = global.Object.prototype.hasOwnProperty('__defineGetter__');
	var ERR_ACCESSORS_NOT_SUPPORTED = 'Getters & setters cannot be defined on this javascript engine';
	var ERR_VALUE_ACCESSORS = 'A property cannot both have accessors and be writable or have a value';
        var Object = global.Object;

	Object.defineProperty = function defineProperty(object, property, descriptor) {

		var propertyString = String(property);
		var hasValueOrWritable = 'value' in descriptor || 'writable' in descriptor;
		var getterType = 'get' in descriptor && typeof descriptor.get;
		var setterType = 'set' in descriptor && typeof descriptor.set;

		if (object === null || !(object instanceof Object || typeof object === 'object')) {
			throw new TypeError('Object must be an object (Object.defineProperty polyfill)');
		}

		if (!(descriptor instanceof Object)) {
			throw new TypeError('Descriptor must be an object (Object.defineProperty polyfill)');
		}

		// handle descriptor.get
		if (getterType) {
			if (getterType !== 'function') {
				throw new TypeError('Getter expected a function (Object.defineProperty polyfill)');
			}
			if (!supportsAccessors) {
				throw new TypeError(ERR_ACCESSORS_NOT_SUPPORTED);
			}
			if (hasValueOrWritable) {
				throw new TypeError(ERR_VALUE_ACCESSORS);
			}
			object.__defineGetter__(propertyString, descriptor.get);
		} else {
			object[propertyString] = descriptor.value;
		}

		// handle descriptor.set
		if (setterType) {
			if (setterType !== 'function') {
				throw new TypeError('Setter expected a function (Object.defineProperty polyfill)');
			}
			if (!supportsAccessors) {
				throw new TypeError(ERR_ACCESSORS_NOT_SUPPORTED);
			}
			if (hasValueOrWritable) {
				throw new TypeError(ERR_VALUE_ACCESSORS);
			}
			object.__defineSetter__(propertyString, descriptor.set);
		}

		// OK to define value unconditionally - if a getter has been specified as well, an error would be thrown above
		if ('value' in descriptor) {
			object[propertyString] = descriptor.value;
		}

		return object;
	};
}(this));
}

// Array.isArray
if (!('isArray' in Array)) {
(function (toString) {
	Object.defineProperty(Array, 'isArray', {
		configurable: true,
		value: function isArray(object) {
			return toString.call(object) === '[object Array]';
		},
		writable: true
	});
}(Object.prototype.toString));

}

// Promise
// polyfill.io serves Yaku, which is not worker-safe; I've subbed in
// github:taylorhakes/promise-polyfill, which is.
if (!('Promise' in this)) {
(function (root) {

  // Store setTimeout reference so promise-polyfill will be unaffected by
  // other code modifying setTimeout (like sinon.useFakeTimers())
  var setTimeoutFunc = root.setTimeout;

  function noop() {}

  // Polyfill for Function.prototype.bind
  function bind(fn, thisArg) {
    return function () {
      fn.apply(thisArg, arguments);
    };
  }

  function Promise(fn) {
    if (typeof this !== 'object') throw new TypeError('Promises must be constructed via new');
    if (typeof fn !== 'function') throw new TypeError('not a function');
    this._state = 0;
    this._handled = false;
    this._value = undefined;
    this._deferreds = [];

    doResolve(fn, this);
  }

  function handle(self, deferred) {
    while (self._state === 3) {
      self = self._value;
    }
    if (self._state === 0) {
      self._deferreds.push(deferred);
      return;
    }
    self._handled = true;
    Promise._immediateFn(function () {
      var cb = self._state === 1 ? deferred.onFulfilled : deferred.onRejected;
      if (cb === null) {
        (self._state === 1 ? resolve : reject)(deferred.promise, self._value);
        return;
      }
      var ret;
      try {
        ret = cb(self._value);
      } catch (e) {
        reject(deferred.promise, e);
        return;
      }
      resolve(deferred.promise, ret);
    });
  }

  function resolve(self, newValue) {
    try {
      // Promise Resolution Procedure: https://github.com/promises-aplus/promises-spec#the-promise-resolution-procedure
      if (newValue === self) throw new TypeError('A promise cannot be resolved with itself.');
      if (newValue && (typeof newValue === 'object' || typeof newValue === 'function')) {
        var then = newValue.then;
        if (newValue instanceof Promise) {
          self._state = 3;
          self._value = newValue;
          finale(self);
          return;
        } else if (typeof then === 'function') {
          doResolve(bind(then, newValue), self);
          return;
        }
      }
      self._state = 1;
      self._value = newValue;
      finale(self);
    } catch (e) {
      reject(self, e);
    }
  }

  function reject(self, newValue) {
    self._state = 2;
    self._value = newValue;
    finale(self);
  }

  function finale(self) {
    if (self._state === 2 && self._deferreds.length === 0) {
      Promise._immediateFn(function() {
        if (!self._handled) {
          Promise._unhandledRejectionFn(self._value);
        }
      });
    }

    for (var i = 0, len = self._deferreds.length; i < len; i++) {
      handle(self, self._deferreds[i]);
    }
    self._deferreds = null;
  }

  function Handler(onFulfilled, onRejected, promise) {
    this.onFulfilled = typeof onFulfilled === 'function' ? onFulfilled : null;
    this.onRejected = typeof onRejected === 'function' ? onRejected : null;
    this.promise = promise;
  }

  /**
   * Take a potentially misbehaving resolver function and make sure
   * onFulfilled and onRejected are only called once.
   *
   * Makes no guarantees about asynchrony.
   */
  function doResolve(fn, self) {
    var done = false;
    try {
      fn(function (value) {
        if (done) return;
        done = true;
        resolve(self, value);
      }, function (reason) {
        if (done) return;
        done = true;
        reject(self, reason);
      });
    } catch (ex) {
      if (done) return;
      done = true;
      reject(self, ex);
    }
  }

  Promise.prototype['catch'] = function (onRejected) {
    return this.then(null, onRejected);
  };

  Promise.prototype.then = function (onFulfilled, onRejected) {
    var prom = new (this.constructor)(noop);

    handle(this, new Handler(onFulfilled, onRejected, prom));
    return prom;
  };

  Promise.all = function (arr) {
    //postMessage({status: "log", "message": "got here Pa"});
    var args = Array.prototype.slice.call(arr);

    return new Promise(function (resolve, reject) {
      if (args.length === 0) { resolve([]); return; }
      var remaining = args.length;
      //postMessage({status: "log", "message": "got here Pa/" + remaining});

      function res(i, val) {
        try {
          if (val && (typeof val === 'object' || typeof val === 'function')) {
            var then = val.then;
            if (typeof then === 'function') {
              then.call(val, function (val) {
                res(i, val);
              }, reject);
              return;
            }
          }
          args[i] = val;
          if (--remaining === 0) {
            resolve(args);
          }
        } catch (ex) {
          reject(ex);
        }
      }

      for (var i = 0; i < args.length; i++) {
        res(i, args[i]);
      }
    });
  };

  Promise.resolve = function (value) {
    //postMessage({status: "log", "message": "got here Pr"});
    if (value && typeof value === 'object' && value.constructor === Promise) {
      return value;
    }

    return new Promise(function (resolve) {
      resolve(value);
    });
  };

  Promise.reject = function (value) {
    return new Promise(function (resolve, reject) {
      reject(value);
    });
  };

  Promise.race = function (values) {
    return new Promise(function (resolve, reject) {
      for (var i = 0, len = values.length; i < len; i++) {
        values[i].then(resolve, reject);
      }
    });
  };

  // Use polyfill for setImmediate for performance gains
  Promise._immediateFn = (typeof root.setImmediate === 'function' && function (fn) { root.setImmediate(fn); }) ||
    function (fn) {
      setTimeoutFunc(fn, 0);
    };

  Promise._unhandledRejectionFn = function _unhandledRejectionFn(err) {
    if (typeof console !== 'undefined' && console) {
      console.warn('Possible Unhandled Promise Rejection:', err); // eslint-disable-line no-console
    }
  };

  /**
   * Set the immediate function to execute callbacks
   * @param fn {function} Function to execute
   * @deprecated
   */
  Promise._setImmediateFn = function _setImmediateFn(fn) {
    Promise._immediateFn = fn;
  };

  /**
   * Change the function to execute on unhandled rejection
   * @param {function} fn Function to execute on unhandled rejection
   * @deprecated
   */
  Promise._setUnhandledRejectionFn = function _setUnhandledRejectionFn(fn) {
    Promise._unhandledRejectionFn = fn;
  };
  root.Promise = Promise;
})(this);
}

// Date.now
if (!('Date' in this && 'now' in this.Date && 'getTime' in this.Date.prototype)) {
this.Date.now = function now() {
	return new Date().getTime();
};

}

if (!('performance' in this && 'now' in this.performance)) {

// performance.now
(function (global) {

var
startTime = Date.now();

if (!global.performance) {
    global.performance = {};
}

global.performance.now = function () {
    return Date.now() - startTime;
};

}(this));

}


})
.call('object' === typeof window && window || 'object' === typeof self && self || 'object' === typeof global && global || {});

// End of manually patched polyfill.
//postMessage({status: "log", message: "got here 0a"});
(function worker_scope (g) {
    "use strict";

    //g.postMessage({status: "log", message: "got here 1"});

    var Array          = g.Array,
        Object         = g.Object,
        Promise        = g.Promise,
        XMLHttpRequest = g.XMLHttpRequest,

        Math           = g.Math,
        floor          = Math.floor,
        random         = Math.random,

        // IE (even latest Edge) doesn't expose performance.now to
        // web workers, and polyfill.io does not know that.
        performance    = g.performance || g.Date,
        now            = performance.now.bind(performance),

        postMessage    = g.postMessage.bind(g),
        setTimeout     = g.setTimeout.bind(g),
        setInterval    = g.setInterval.bind(g),
        clearInterval  = g.clearInterval.bind(g);

    //g.postMessage({status: "log", message: "got here 2"});
    //postMessage({status: "log", message: "got here 2a"});

    g.onerror = function onerror (err) {
        var msg;
        if (err.message)
            msg = err.message;
        else
            msg = err.toString();
        if (err.name)
            msg = err.name + ": " + msg;
        if (err.stack)
            msg = msg + "\n" + err.stack;
        postMessage({ status: "error", error: err, message: msg });
    };
    //postMessage({status: "log", message: "got here 2b"});

    // Fisher-Yates in-place shuffle, from
    // https://www.frankmitchell.org/2015/01/fisher-yates/
    function shuffle (arr) {
        var i, j, temp;
        for (i = arr.length - 1; i > 0; i--) {
            j = floor(random() * (i+1));
            temp = arr[i];
            arr[i] = arr[j];
            arr[j] = temp;
        }
    }

    function rate_limit (fn, delay, concurrent, context) {
        var queue = [], pending = 0, timer = null;
        //postMessage({status: "log", message: "got here rl/" + delay + "/" + concurrent});

        function RLTask (context, args) {
  	    this.context = context;
            this.args = args;
            // We need the Promise now so it can be available to
            // limited()'s caller, but we do not actually want to _do_
            // anything until run() is called.  Thus, a shim executor
            // that just records the resolve/reject hooks.
            this.promise = new Promise((function (resolve, reject) {
		this._resolve = resolve;
                this._reject = reject;
            }).bind(this));
            this.promise.then(complete_task, complete_task);
        }
        RLTask.prototype.run = function run () {
            //postMessage({status: "log", message: "got here rl/run"});
            pending++;
  	    try {
    	        this._resolve(fn.apply(this.context, this.args));
            } catch (e) {
    	        this._reject(e);
            }
        };
        function complete_task () {
  	    pending--;
            maybe_start_task();
        }
        function maybe_start_task () {
            //postMessage({status: "log", message: "got here rl/mst " + queue.length + "/" + pending});
  	    if (queue.length === 0) {
    	        if (timer !== null) {
      	            clearInterval(timer);
                    timer = null;
                }
    	        return;
            }
            if (timer == null)
    	        timer = setInterval(maybe_start_task, delay);

            if (pending >= concurrent)
    	        return;

            //postMessage({status: "log", message: "got here rl/willrun"});
            queue.shift().run();
        }

        return function limited () {
            //postMessage({status: "log", message: "got here rl/q+ " + queue.length + "/" + pending});
  	    var task = new RLTask(context || this, Array.prototype.slice.call(arguments));
            queue.push(task);
            //postMessage({status: "log", message: "got here rl/q++ " + queue.length + "/" + pending});
            if (queue.length === 1) {
                //postMessage({status: "log", message: "got here rl/mst+ "});
                setTimeout(maybe_start_task, 0);
            }
            return task.promise;
        };
    }

    function probe_one (addr, port, timeout) {
        //postMessage({status: "log", message: "got here p1/"+addr});
        return new Promise(function (resolve, reject) {
            var start = 0, stop = null, xhr;
            function loadstart () {
                try {
                    //postMessage({status: "log", message: "got here p+/"+addr});
                    start = now();
                } catch (e) {
                    //postMessage({status: "log", message: "got here p+C/"+addr + ' ' + e.message});
                    reject(e);
                }
            }
            function loadstop () {
                if (stop === null) {
                    try {
                        //postMessage({status: "log", message: "got here p-/"+addr});
                        stop = now();
                        xhr.abort();
                        if (stop - start >= timeout - 10) {
                            reject(new Error("connection timeout"));
                        } else {
                            resolve(stop - start);
                        }
                    } catch (e) {
                        //postMessage({status: "log", message: "got here p-C/"+addr + ' ' + e.message});
                        reject(e);
                    }
                }
            }

            //postMessage({status: "log", message: "got here p2/"+addr});
            try {
                xhr = new XMLHttpRequest();

                xhr.timeout = timeout;
                xhr.addEventListener("loadstart", loadstart);
                // This API does not reveal the difference between
                // ECONNREFUSED and EHOSTUNREACH, alas.
                xhr.addEventListener("error", loadstop);
                xhr.addEventListener("timeout", loadstop);
                // We set all three of these Just To Be Sure and also because
                // one of them may fire ever so slightly earlier.
                xhr.addEventListener("load", loadstop);
                xhr.addEventListener("loadend", loadstop);
                xhr.addEventListener("readystatechange", function () {
                    if (xhr.readyState === 2 || xhr.readyState === 4)
                        loadstop();
                });

                //postMessage({status: "log", message: "got here p3/"+addr});
                xhr.open("HEAD",
                         "https://" + addr + ":" + port + "/?" +
                         floor(random() * 1e10));
                //postMessage({status: "log", message: "got here p4/"+addr});
                xhr.send();

            } catch (e) {
                //postMessage({status: "log", message: "got here pC/"+addr + ' ' + e.message});
                reject(e);
            }
        });
    }

    function run(opts) {
        //postMessage({status: "log", message: "got here R0"});
        var config       = opts.config,
            rl_probe_one = rate_limit(probe_one,
                                      config.spacing, config.parallel),
            landmarks    = opts.landmarks,
            addrs        = Object.keys(landmarks),
            probe_order  = [],
            i, j, lm;

        //postMessage({status: "log", "message": "got here R1"});

        // Probe each host all at once, but do them in a random order.
        shuffle(addrs);

        // If there is an entry in the landmarks list with no location,
        // we need to do that one first, because it's the local address
        // and ui.js will use it to estimate overhead.
        for (i = 0; i < addrs.length; i++) {
            lm = landmarks[addrs[i]];
            if (lm.lat === 0 && lm.lon === 0
                && lm.cbg_m === 0 && lm.cbg_b === 0) {
                for (j = 0; j < config.n_probes; j++) {
                    probe_order.push(addrs[i]);
                }
            }
        }
        for (i = 0; i < addrs.length; i++) {
            lm = landmarks[addrs[i]];
            if (lm.lat !== 0 || lm.lon !== 0
                || lm.cbg_m !== 0 || lm.cbg_b !== 0) {
                for (j = 0; j < config.n_probes; j++) {
                    probe_order.push(addrs[i]);
                }
            }
        }


        //postMessage({status: "log", message: "got here R2"});
        return Promise.all(probe_order.map(function (addr) {
            var lm = landmarks[addr];
            // Wrapper Promise which is always resolved successfully,
            // so that the .all() operation is not interrupted by a single
            // failed probe.
            return new Promise(function (resolve, reject) {
                //postMessage({status: "log", message: "got here W/"+addr});
                rl_probe_one(addr, lm.port, config.timeout)
                    .then(function (elapsed) {
                        postMessage({ status: "rtt",
                                      addr: addr,
                                      port: lm.port,
                                      elapsed: elapsed });
                        resolve();
                    })
                    .catch(function (err) {
                        postMessage({ status: "rtt_error",
                                      addr: addr,
                                      port: lm.port,
                                      error: err.toString() });
                        resolve();
                    });
            });
        }));
    }
    g.onmessage = function onmessage (msg) {
        //postMessage({status:"log", message: "got here OM/"+msg.data.op});
        if (msg.data.op === "close") {
            g.close();
            return;
        } else if (msg.data.op !== "probe") {
            postMessage({ status: "error",
                          error: "unknown op: " + msg.data.op });
            g.close();
            return;
        }

        Promise.resolve(msg.data)
        .then(run)
        .then(function () {
            postMessage({ status: "done" });
        })
        .catch(function (err) {
            postMessage({ status: "error", error: err });
            g.close();
        });
    };

    postMessage({ status: "ready" });

}(self));
