/*global proj4,ol*/
(function client_namespace(w, d, p4, ol) {
    "use strict";
    var logElement;
    var geo = null;
    var goButton;
    var paramsForm;
    var worldMap;

    function log(msg) {
        logElement.appendChild(d.createTextNode(msg + "\n"));
    }
    function logParams() {
        var elts = paramsForm.elements;
        var i = 0;
        var e = null;
        for (i = 0; i < elts.length; i++) {
            e = elts[i];
            switch (e.type) {
            case 'submit':
            case 'fieldset':
                break;
            case 'radio':
            case 'checkbox':
                if (e.checked)
                    log("[" + e.type + "] " + e.name + " = " + e.value);
                break;
            default:
                log("[" + e.type + "] " + e.name + " = " + e.value);
            }
        }
    }

    function RateLimit (fn, delay, concurrent, context) {
        var queue = [], pending = 0, timer = null;

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
  	    if (queue.length == 0) {
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

            queue.shift().run();
        }

        return function limited () {
  	    var task = new RLTask(context || this, Array.from(arguments));
            queue.push(task);
            if (queue.length === 1)
                setTimeout(maybe_start_task, 0);
            return task.promise;
        };
    }



    function getBrowserLocation() {
        if (geo === null) return;

        geo.getCurrentPosition(
            function geo_success (pos) {
                log("browser-lat = " + pos.coords.latitude);
                log("browser-lon = " + pos.coords.longitude);
                addDot('browserLoc', pos.coords.latitude, pos.coords.longitude);
                d.getElementById('browser-lat').value = pos.coords.latitude;
                d.getElementById('browser-lon').value = pos.coords.longitude;
            },
            function geo_fail (e) {
                log("geolocation failure: " + e.message +
                    " (code " + e.code + ")");
            }
        );
    }


    function prepareForm() {
        var proxyNo      = d.getElementById("proxy-no");
        var proxyUnknown = d.getElementById("proxy-unknown");
        var proxyCoords  = d.getElementById("proxy-coords");
        var proxyLat     = d.getElementById("proxy-lat");
        var proxyLon     = d.getElementById("proxy-lon");

        goButton = d.getElementById("go");
        paramsForm = d.getElementById("user_params");

        function proxyButtonClicked() {
            if (proxyCoords.checked) {
                proxyLat.disabled = false;
                proxyLon.disabled = false;
                proxyLat.required = true;
                proxyLon.required = true;
            } else {
                proxyLat.disabled = true;
                proxyLon.disabled = true;
                proxyLat.required = false;
                proxyLon.required = false;
            }
        }
        proxyNo.onclick      = proxyButtonClicked;
        proxyUnknown.onclick = proxyButtonClicked;
        proxyCoords.onclick  = proxyButtonClicked;
        // If the page is reloaded, the state of the buttons may be preserved.
        proxyButtonClicked();

        if ("geolocation" in w.navigator) {
            geo = w.navigator.geolocation;
        } else {
            d.getElementById("will-geoloc").style.display = "none";
        }

        paramsForm.addEventListener("submit", runExperiment);
        goButton.innerHTML = "Run the experiment";
        goButton.disabled = false;
    }

    // As best I can tell, you can only specify styles on a per-layer basis.
    var dotStyles = {
        'trueLoc': new ol.style.Style({
            image: new ol.style.Circle({
                radius: 2,
                snapToPixel: false,
                fill: new ol.style.Fill({color: 'green'}),
                stroke: new ol.style.Stroke({color: 'blue', width: 1})
            })
        }),
        'browserLoc': new ol.style.Style({
            image: new ol.style.Circle({
                radius: 2,
                snapToPixel: false,
                fill: new ol.style.Fill({color: 'green'}),
                stroke: new ol.style.Stroke({color: 'red', width: 1})
            })
        }),
        'proxyLoc': new ol.style.Style({
            image: new ol.style.Circle({
                radius: 2,
                snapToPixel: false,
                fill: new ol.style.Fill({color: 'blue'}),
                stroke: new ol.style.Stroke({color: 'red', width: 1})
            })
        }),
        'landmark': new ol.style.Style({
            image: new ol.style.Circle({
                radius: 1,
                snapToPixel: false,
                fill: new ol.style.Fill({color: '#888'}),
                stroke: new ol.style.Stroke({color: 'blue', width: 1})
            })
        })
    };
    var dotCollections = {
        'trueLoc': new ol.Collection(),
        'browserLoc': new ol.Collection(),
        'proxyLoc': new ol.Collection(),
        'landmark': new ol.Collection()
    };
    function addDot(style, lat, lon) {
        // for some goddamned reason, we have to manually project from
        // lon/lat coordinates, even though logically that is the view's job.
        var projected = ol.proj.transform([lon, lat], "EPSG:4326", "ESRI:53009");
        dotCollections[style].push(new ol.Feature(
            new ol.geom.Point(projected)));
    }

    function showKnownLocs() {
        var client_lat = d.getElementById('client-lat').value;
        var client_lon = d.getElementById('client-lon').value;
        addDot('trueLoc', +client_lat, +client_lon);
        if (d.getElementById('proxy-coords').checked) {
            var proxy_lat = d.getElementById('proxy-lat').value;
            var proxy_lon = d.getElementById('proxy-lon').value;
            addDot('proxyLoc', +proxy_lat, +proxy_lon);
        }
    }

    function prepareMap() {
        p4.defs('ESRI:53009',
                '+proj=moll +lon_0=0 +x_0=0 +y_0=0 +a=6371000 ' +
                '+b=6371000 +units=m +no_defs');

        // Configure the projection object with an extent,
        // and a world extent. These are required for the Graticule.
        var projection = new ol.proj.Projection({
            code: 'ESRI:53009',
            extent: [-9009954.605703328, -9009954.605703328,
                     9009954.605703328, 9009954.605703328],
            worldExtent: [-179, -89.99, 179, 89.99]
        });

        worldMap = new ol.Map({
            keyboardEventTarget: d,
            layers: [
                new ol.layer.Vector({
                    source: new ol.source.Vector({
                        url: 'http://openlayers.org/en/v3.18.2/examples/data/geojson/countries-110m.geojson',
                        format: new ol.format.GeoJSON()
                    })
                }),
                new ol.layer.Vector({
                    source: new ol.source.Vector({ features: dotCollections['landmark'] }),
                    style: dotStyles['landmark']
                }),
                new ol.layer.Vector({
                    source: new ol.source.Vector({ features: dotCollections['browserLoc'] }),
                    style: dotStyles['browserLoc']
                }),
                new ol.layer.Vector({
                    source: new ol.source.Vector({ features: dotCollections['proxyLoc'] }),
                    style: dotStyles['proxyLoc']
                }),
                new ol.layer.Vector({
                    source: new ol.source.Vector({ features: dotCollections['trueLoc'] }),
                    style: dotStyles['trueLoc']
                })
            ],
            renderer: 'canvas',
            target: 'map',
            view: new ol.View({
                center: [0, 0],
                projection: projection,
                resolutions: [65536, 32768, 16384, 8192, 4096, 2048],
                zoom: 0
            })
        });

        new ol.Graticule({
            map: worldMap
        });
    }

    function runExperiment(ev) {
        ev.preventDefault();
        goButton.disabled = true;
        goButton.innerHTML = "Running…";

        logParams();
        showKnownLocs();
        getBrowserLocation();
    }

    w.onload = function client_onload () {
        logElement = d.getElementById("log");
        log("got here 1");

        prepareForm();
        log("got here 2");

        prepareMap();
        log("got here 3");

        var mainEl = d.getElementsByTagName("main")[0];
        mainEl.className = "browserok";
        log("ready");
    };
})(window, document, proj4, ol);
