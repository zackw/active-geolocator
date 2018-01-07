(function ui_scope(window, undefined) {
    "use strict";

    var d = window.document,
        setTimeout = window.setTimeout.bind(window),
        clearTimeout = window.clearTimeout.bind(window),
        addGlobalEventListener = window.addEventListener.bind(window),
        parseFloat = window.parseFloat,
        p4,
        ol,
        wgs,
        fetch,
        FormData,
        Promise,
        Worker,
        geo,

        log_element,
        progress_element,
        fatal_err_element,
        go_button,
        params_form,
        world_map,
        cbg_circles,
        cbg_circle_src,
        n_landmarks,
        landmark_data,
        worker,
        browser_short_version,
        still_ok = true,
        loaded = false,
        load_timeout = null,
        browser_lat = null,
        browser_lon = null,
        geoip_lat = null,
        geoip_lon = null,
        pending_pins = [],
        probe_successes = 0,
        probe_failures = 0,

        config = {
            // Where to find static resources (relative to index.html).
            // Must end with a slash.
            static_base: "static/",
            // URL of the list of addresses to probe.
            landmark_url: "api/1/landmark-list-with-locations",
            // URL to push results back to.
            results_url: "api/1/probe-results",
            // Time between successive probes (milliseconds)
            spacing: 10,
            // Maximum number of concurrent probes to perform.
            // Most browsers have both a limit on the number of concurrent
            // requests to the same server, and a limit on the total number
            // of concurrent requests.  The latter varies but is rarely less
            // than 5 or more than 10.
            parallel: 5,
            // Connection timeout (milliseconds)
            timeout: 10000,
            // Number of times to probe each landmark
            n_probes: 5,

            // CBG tuning parameters - see calibration.py for rationale
            cbg_dist_limit: 20037508, // ½ equatorial circumf. of Earth (m)
            cbg_time_limit: 273.16,   // max plausible time to cover that (ms)
            cbg_overhead_est: 25,     // estimated overhead to subtract (ms)

            // Ports that XMLHttpRequest is not allowed to connect to.
            // See https://fetch.spec.whatwg.org/#port-blocking
            // and https://developer.mozilla.org/en-US/docs/Mozilla/Mozilla_Port_Blocking
            blocked_ports: {
                1: true,    7: true,    9: true,    11: true,   13: true,
                15: true,   17: true,   19: true,   20: true,   21: true,
                22: true,   23: true,   25: true,   37: true,   42: true,
                43: true,   53: true,   77: true,   79: true,   87: true,
                95: true,   101: true,  102: true,  103: true,  104: true,
                109: true,  110: true,  111: true,  113: true,  115: true,
                117: true,  119: true,  123: true,  135: true,  139: true,
                143: true,  179: true,  389: true,  465: true,  512: true,
                513: true,  514: true,  515: true,  526: true,  530: true,
                531: true,  532: true,  540: true,  556: true,  563: true,
                587: true,  601: true,  636: true,  993: true,  995: true,
                2049: true, 3659: true, 4045: true, 6000: true, 6665: true,
                6666: true, 6667: true, 6668: true, 6669: true
            }

        },
        // Bigger circles are drawn in lighter, more transparent colors.
        // m = max circle radius in meters; f = fill color, rgba;
        // s = stroke color, rgba.  20037508m is half the equatorial
        // circumference of the Earth -- it's not possible for the
        // target to be farther away from a landmark than that.
        // Each step is 4x larger than the previous.  The fill and
        // stroke colors are the darkest 4 steps of 4- and 7-class
        // Purples, respectively, from colorbrewer2.org.
        circle_styles = [
            { m:   313086.0625, f:[ 84, 39,143,0.20], s:[ 74, 20,134,0.9] },
            { m:  1252344.2500, f:[117,107,177,0.10], s:[106, 81,163,0.8] },
            { m:  5009377.0000, f:[158,154,200,0.05], s:[128,125,186,0.7] },
            { m: 10018754.0000, f:[203,201,226,0.01], s:[158,154,200,0.6] },
//          { m: 20037508.0000, f:[203,201,226,0.01], s:[158,154,200,0.6] },
        ];

    /* Logging and error reporting */

    function clear_log() {
        log_element.innerHTML = "";
    }
    function log(msg) {
        log_element.appendChild(d.createTextNode(msg + "\n"));
        log_element.scrollTop = log_element.scrollHeight;
    }
    function error_to_string(err) {
        if (typeof err !== "object") {
            return ""+err;
        } else if ("status" in err) {
            if ("message" in err) {
                return err.message;
            } else if ("error" in err) {
                return error_to_string(err.error);
            } else {
                return "unexpected worker message: " + JSON.stringify(err);
            }
        } else {
            var msg;
            if (err.message)
                msg = err.message;
            else
                msg = err.toString();
            if (err.name)
                msg = err.name + ": " + msg;
            if (err.stack)
                msg = msg + "\n" + err.stack;
            return msg;
        }
    }

    function show_fatal_error (label, err) {
        var msg;

        if (load_timeout) {
            clearTimeout(load_timeout);
            load_timeout = null;
        }
        if (worker) {
            worker.terminate();
            worker = null;
        }
        if (err) {
            msg = error_to_string(err);
            console.error(err);
        } else {
            msg = "??? show_fatal_error called with no message";
            console.error(msg);
        }
        log(label + msg);
        if (loaded) {
            var text = "Error while running the demo.";
            if (/\b(safari|webkit|ie)\b/i.test(browser_short_version))
                text += "<br>(This demo demands a lot from your browser.\
 You may have better luck with Chrome, Firefox, or MS Edge.)";
            text += "<br>You can see more details in the “technical log” below.";
            fatal_err_element.innerHTML = text;
        } else {
            fatal_err_element.innerHTML = "Unfortunately,\
 your browser does not support all of the JavaScript features\
 that this demonstration needs.  You can see more details in the\
 “technical log” below.";
        }
        still_ok = false;
        show_id("error_message");
        hide_id("loading_message");
        hide_id("map_container");
        hide_id("browser_ok");
        hide_id("after_demo");
    }

    function fatal_error (err) {
        show_fatal_error("Fatal error: ", err);
    }
    function worker_error (err) {
        show_fatal_error("Fatal error in worker: ", err);
    }

    /* User interface */

    function show_class(cls) {
        var elts = d.getElementsByClassName(cls);
        for (var i = 0; i < elts.length; i++)
            elts[i].className = elts[i].className.replace(/\bhide\b/g, '');
    }

    function hide_class(cls) {
        var elts = d.getElementsByClassName(cls);
        for (var i = 0; i < elts.length; i++)
            elts[i].className += ' hide';
    }

    function show_id(id) {
        var elt = d.getElementById(id);
        elt.className = elt.className.replace(/\bhide\b/g, '');
    }

    function hide_id(id) {
        var elt = d.getElementById(id);
        elt.className += ' hide';
    }

    function progress_bar_begin(max) {
        progress_element.max = max;
        progress_element.value = 0;
        progress_element.innerHTML = "0/" + max;
    }
    function progress_bar_tick() {
        progress_element.value += 1;
        progress_element.innerHTML = ("" + progress_element.value +
                                      "/" + progress_element.max);
    }

    // Loosely based on https://github.com/rstacruz/details-polyfill/
    function polyfill_details() {
        var result, diff, i, stylebase, el = d.createElement("details");
        if ('open' in el) {
            el.innerHTML = '<summary>a</summary>b';
            d.body.appendChild(el);
            diff = el.offsetHeight;
            el.open = true;
            result = (diff != el.offsetHeight);
            d.body.removeChild(el);
            if (result) return;
        }

        el = d.createElement("link");
        el.setAttribute("rel", "stylesheet");
        el.setAttribute("href", config.static_base + "details-poly.css");
        d.head.appendChild(el);
        addGlobalEventListener("click", function details_polyfill_click(e) {
            var details;
            if (e.target.nodeName.toLowerCase() === 'summary') {
                details = e.target.parentNode;
                if (!details) return;

                if (details.getAttribute('open')) {
                    details.open = false;
                    details.removeAttribute('open');
                } else {
                    details.open = true;
                    details.setAttribute('open', 'open');
                }
            }
        });
    }

    function excess_precision(val, step)
    {
        var x = val / step;
        return Math.abs(Math.round(x) - x) > 1e-14;
    }

    function validate_form() {
        // We don't use querySelectorAll(":invalid") here because
        // we want this to work regardless of whether the browser has
        // native validity handling.
        var all_ok = true;
        ["client-lat", "client-lon", "proxy-lat", "proxy-lon"].forEach(
            function validate_numeric_elt(id) {
                var elt = d.getElementById(id);
                var ok;
                if (elt.disabled)
                    ok = true;
                else {
                    var val = parseFloat(elt.value);
                    var min = parseFloat(elt.getAttribute("min"));
                    var max = parseFloat(elt.getAttribute("max"));
                    var step = parseFloat(elt.getAttribute("step"));
                    if (min !== min || max !== max || step !== step) {
                        log("bad config for " + id + ": min='" +
                            elt.getAttribute("min") + "' max='" +
                            elt.getAttribute("max") + "' step='" +
                            elt.getAttribute("step") + "'");
                        ok = false;
                    } else if (val !== val)
                        // syntactically invalid number => NaN
                        ok = false;
                    else if (val < min || val > max)
                        ok = false;
                    else if (excess_precision(val, step))
                        ok = false;
                    else
                        ok = true;
                }
                if (ok)
                    hide_id(id + "-ve");
                else
                    show_id(id + "-ve");
                all_ok |= ok;
            });
        return all_ok;
    }

    function prepare_form() {
        var proxy_no      = d.getElementById("proxy-no");
        var proxy_unknown = d.getElementById("proxy-unknown");
        var proxy_coords  = d.getElementById("proxy-coords");
        var proxy_lat     = d.getElementById("proxy-lat");
        var proxy_lon     = d.getElementById("proxy-lon");

        log_element       = d.getElementById("log");
        params_form       = d.getElementById("user_params");
        fatal_err_element = d.getElementById("error_message");
        progress_element  = d.getElementById("run_progress");

        polyfill_details();

        log("Configuration: " + JSON.stringify(config));

        function proxy_button_clicked() {
            if (proxy_coords.checked) {
                proxy_lat.disabled = false;
                proxy_lon.disabled = false;
                proxy_lat.required = true;
                proxy_lon.required = true;
            } else {
                proxy_lat.disabled = true;
                proxy_lon.disabled = true;
                proxy_lat.required = false;
                proxy_lon.required = false;
            }
        }
        proxy_no.onclick      = proxy_button_clicked;
        proxy_unknown.onclick = proxy_button_clicked;
        proxy_coords.onclick  = proxy_button_clicked;
        // If the page is reloaded, the state of the buttons may be preserved.
        proxy_button_clicked();

        d.getElementById("go").addEventListener(
            "click", run_experiment);
        d.getElementById("open_consent_form").addEventListener(
            "click", function () {
                hide_id("after_demo");
                show_id("consent_form");
                var el = d.getElementById("consent_form");
                if ("scrollIntoView" in el)
                    el.scrollIntoView();
                window.location.hash = "#consent_form";
            });

        params_form.addEventListener("submit", function (e) {
            e.preventDefault();
            if (validate_form())
                post_results();
        });
        params_form.addEventListener("invalid", function (e) {
            e.preventDefault(); // suppress default balloons
            validate_form();
        }, true); // invalid events don't bubble,
                  // but can be captured by the form
    }

    /* Map display */
    function load_map() {
        return new Promise(function prepare_map_i (resolve, reject) {
            var scale_line = new ol.control.ScaleLine(),
                scale_control = d.getElementById("map_units"),
                circles = [], features = [], poly, feat, i;

            /* The map must be displayed before ol.Map() runs, or it
               won't work. */
            show_id("map_container");

            for (i = 0; i < circle_styles.length; i++) {
                poly = new ol.geom.MultiPolygon([]);
                feat = new ol.Feature(poly);
                feat.setStyle(new ol.style.Style({
                    fill: new ol.style.Fill({
                        color: circle_styles[i].f
                    }),
                    stroke: new ol.style.Stroke({
                        width: 1,
                        color: circle_styles[i].s
                    })
                }));
                circles.push(poly);
                features.push(feat);
            }
            cbg_circles = circles;
            cbg_circle_src = new ol.source.Vector({
                features: features,
                wrapX: false
            });

            world_map = new ol.Map({
                keyboardEventTarget: d,
                layers: [
                    new ol.layer.Tile({source: new ol.source.OSM({
                        wrapX: false
                    })}),
                    new ol.layer.Vector({source: cbg_circle_src})
                ],
                target: "map",
                view: new ol.View({
                    center: [0, 0],
                    zoom: 1, minZoom: 1
                }),
                controls: ol.control.defaults().extend([
                    scale_line
                ])
            });
            scale_control.addEventListener("change", function (e) {
                scale_line.setUnits(scale_control.value);
            });
            world_map.once("postrender", function () {
                log("Map rendered.");
                resolve();
            });
        });
    }

    function do_place_pin(data) {
        var label = data.label;
        var lonlat = data.lonlat;
        var coord = ol.proj.fromLonLat(lonlat);
        log("Placing '" + label + "' pin at " +
            JSON.stringify(lonlat) + " (projected: " +
            JSON.stringify(coord) + ")");
        world_map.addOverlay(new ol.Overlay({
            element: d.getElementById("pin_" + label),
            positioning: "bottom-center",
            position: coord
        }));
    }

    function place_pending_pins() {
        for (var i = 0; i < pending_pins.length; i++) {
            do_place_pin(pending_pins[i]);
        }
        pending_pins = null;
    }

    function place_pin(label, lat, lon) {
        var data = { label: label, lonlat: [lon, lat] };
        if (loaded)
            do_place_pin(data);
        else
            pending_pins.push(data);
    }

    function place_cbg_circle(lm) {

        function index_of_max_latitude_difference(arc) {
            var i, delta, maxdelta = 0, maxindex;
            for (i = 1; i < arc.length; i++) {
                delta = Math.abs(arc[i-1][1] - arc[i][1]);
                if (delta > maxdelta) {
                    maxdelta = delta;
                    maxindex = i;
                }
            }
            return maxindex;
        }

        // PNPoly algorithm from
        // http://www.codeproject.com/Tips/84226/Is-a-Point-inside-a-Polygon.
        // Note: only works on _simple_ polygons (only one ring).
        function point_in_polygon(pt, poly) {
            var px = pt[0], py = pt[1],
                ext  = poly.getExtent(),
                ring, nvert, i, j, inside;

            // Are we entirely outside the bounding rectangle?
            if (px < ext[0] || px > ext[2] || py < ext[1] || py > ext[3])
                return false;

            ring = poly.getLinearRing(0).getCoordinates();
            nvert = ring.length;
            inside = false;
            for (i = 0, j = nvert-1; i < nvert; j = i++)
                if (((ring[i][1]>py) != (ring[j][1]>py)) &&
	            (px < ((ring[j][0]-ring[i][0]) * (py-ring[i][1]) /
                           (ring[j][1]-ring[i][1]) + ring[i][0])))
                    inside = !inside;

            return inside;
        }

        // Array.map() adapter
        function project_ll(val) { return ol.proj.fromLonLat(val); }

        var minrtt, radius, ring_coords, i, p, pt, prev_pt,
            polys, contained, origin, group;

        if ((!lm.cbg_m && !lm.cbg_b) || (!lm.lat && !lm.lon))
            return;
        minrtt = Math.min.apply(Math, lm.rtts) - config.cbg_overhead_est;
        if (minrtt < 1) minrtt = 1;

        // Physical limits on meaningful RTT and circle radius
        if (minrtt > config.cbg_time_limit) return;
        radius = lm.cbg_m * minrtt + lm.cbg_b;
        radius = Math.min(radius, config.cbg_dist_limit);

        // Select the polygon group to add the circle to, based on its
        // radius.  This controls the drawing style.
        for (i = 0; i < circle_styles.length; i++) {
            if (circle_styles[i].m >= radius) {
                group = cbg_circles[i];
                break;
            }
        }
        if (group === undefined)
            return;

        // OL3's Polygon.circular works on a sphere, not on the geoid.
        // Approximate a circle as a 60-sided polygon.
        // We must manually fix up circles that cross the coordinate
        // singularity at longitude ±180.  Start by assembling
        // unprojected coordinates in two arrays; each time we cross
        // the singularity we switch to the other array.  (This can
        // happen at most twice.)
        ring_coords = [ [], [] ];
        p = 0;
        prev_pt = null;
        for (i = 0; i < 60; i++) {
            // 60*6 = 360
            pt = wgs.Direct(lm.lat, lm.lon, i*6, radius);
            if (prev_pt !== null && Math.abs(prev_pt.lon2 - pt.lon2) > 180)
                p++;
            // To avoid projection singularities, latitudes are clipped at ±85.
            ring_coords[p%2].push([pt.lon2, Math.min(85, Math.max(-85, pt.lat2))]);
            prev_pt = pt;
        }
        // Count a singularity crossing that happens between the first
        // and last points.
        if (Math.abs(ring_coords[0][0][0] -
                     ring_coords[p%2][ring_coords[p%2].length-1][0]) > 180)
            p++;

        if (p === 0) {
            // The circle did not cross the singularity.  Discard the
            // empty second list.
            ring_coords.pop();

        } else if (p === 1) {
            // The circle crossed the singularity once.  That means it
            // traverses the entire breadth of the map and encloses
            // one of the poles.  Recombine the two arrays into one,
            // rotating the coordinate list so that the break is at
            // either end, then insert a diversion to either the north
            // or south pole, whichever is closer.
            var arc = ring_coords[1].concat(ring_coords[0]);
            if (arc[0][0] > 0)
                arc.reverse();

            var pole = (arc[0][1] > 0) ? 85 : -85;
            if (arc[0][1] !== pole) {
                if (arc[0][0] === -180)
                    arc.unshift([-180, pole]);
                else
                    arc.unshift([-180, pole], [-180, arc[0][1]]);
            }

            if (arc[arc.length-1][1] !== pole) {
                if (arc[arc.length-1][0] === 180)
                    arc.push([180, pole]);
                else
                    arc.push([180, arc[arc.length-1][1]], [180, pole]);
            }

            ring_coords = [ arc ];

        } else if (p === 2) {
            // The circle crossed the singularity twice.  It does not
            // enclose a pole, and should be represented as two
            // patches, one on one side of the map and one on the
            // other.  Make sure each actually touches the ±180 line.
            var arc0 = ring_coords[0], arc1 = ring_coords[1];

            // arc1 already begins and ends at the singularity, but
            // arc0 needs to be rotated.
            var cut = index_of_max_latitude_difference(arc0);
            arc0 = arc0.slice(cut).concat(arc0.slice(0, cut));

            if (Math.abs(arc0[0][0]) !== 180)
                arc0.unshift([180 * Math.sign(arc0[0][0]),
                              arc0[0][1]]);
            if (Math.abs(arc0[arc0.length-1][0]) !== 180)
                arc0.push([180 * Math.sign(arc0[arc0.length-1][0]),
                           arc0[arc0.length-1][1]]);

            if (Math.abs(arc1[0][0]) !== 180)
                arc1.unshift([180 * Math.sign(arc1[0][0]),
                              arc1[0][1]]);
            if (Math.abs(arc1[arc1.length-1][0]) !== 180)
                arc1.push([180 * Math.sign(arc1[arc1.length-1][0]),
                           arc1[arc1.length-1][1]]);

            ring_coords = [ arc0, arc1 ];

        } else {
            log("impossible: circle cut " + p + "times -- " +
                JSON.stringify(ring_coords));
            return;
        }

        // If you pass more than one arc at a time to Polygon(), the
        // second-and-subsequent arcs are treated as "interior"
        // subtractive geometry, which is not what we want in the
        // two-patch case; that has to be modeled as two polygons.
        polys = ring_coords.map(function (arc) {
            return new ol.geom.Polygon([arc.map(project_ll)]);
        });
        // One final special case: It's possible that we have ended up
        // with the _complement_ of the shape we actually want.  We
        // must verify that at least one of the polygons encloses the
        // origin point.  OL3 doesn't appear to have any native way to
        // do this.
        contained = false;
        origin = project_ll([lm.lon, lm.lat]);
        for (i = 0; i < polys.length; i++) {
            if (point_in_polygon(origin, polys[i])) {
                contained = true;
                break;
            }
        }


        if (contained) {
            for (i = 0; i < polys.length; i++)
                group.appendPolygon(polys[i]);
        } else {
            // The polygons do not contain the origin.  Subtract them
            // from the bounding-box of the map.
            var bb = ol.geom.Polygon.fromExtent(
                project_ll([-180, -85]).concat(project_ll([180, 85])));
            for (i = 0; i < polys.length; i++) {
                bb.appendLinearRing(polys[i].getLinearRing(0));
            }
            group.appendPolygon(bb);
        }
        cbg_circle_src.changed();
    }

    /* Core experiment (more in worker.js) */
    function get_browser_location() {
        if (!geo) {
            log("geolocation unavailable");
            return;
        }

        geo.getCurrentPosition(
            function geo_success (pos) {
                browser_lat = pos.coords.latitude;
                browser_lon = pos.coords.longitude;
                log("location from browser: " + pos.coords.latitude +
                    ", " + pos.coords.longitude);
                place_pin("browser", browser_lat, browser_lon);
                show_class("gl_b_or_f");
                show_class("gl_b");
                if (geoip_lat === null) {
                    show_class("gl_b_xor_f");
                } else {
                    hide_class("gl_b_xor_f");
                    show_class("gl_b_and_f");
                }
            },
            function geo_fail (e) {
                console.error("geolocation failure:", e);
                log("geolocation failure: " + error_to_string(e));
            }
        );
    }

    function fetch_assert_ok (response) {
        if (!response.ok) {
            var err = new Error(response.url + ": got " +
                                response.status + " " +
                                response.statusText);
            err.response = response;
            throw err;
        }
        return Promise.resolve();
    }

    function fetch_decode_json (response) {
        fetch_assert_ok(response);
        return response.json();
    }

    function fetch_maybe_decode_json (response) {
        fetch_assert_ok(response);
        if (response.status === 204) {
            return Promise.resolve({});
        } else {
            return response.json();
        }
    }

    function get_geoip_location () {
        var el  = document.getElementById("offstage"),
            lon = parseFloat(el.getAttribute("data-geoip-lon")),
            lat = parseFloat(el.getAttribute("data-geoip-lat"));
        if (Math.abs(lon) > 0.5 && Math.abs(lat) > 0.5) {
            geoip_lat = lat;
            geoip_lon = lat;
            log("location from geoip: " + geoip_lat +
                ", " + geoip_lon);
            place_pin("geoip", geoip_lat, geoip_lon);
            show_class("gl_b_or_f");
            show_class("gl_f");
            if (browser_lat === null) {
                show_class("gl_b_xor_f");
            } else {
                hide_class("gl_b_xor_f");
                show_class("gl_b_and_f");
            }
        }
    }


    function load_landmarks() {
        return fetch(config.landmark_url)
            .then(fetch_decode_json)
            .then(function (landmarks) {
                var probes, i, val, port;
                landmark_data = {};
                n_landmarks = landmarks.length;
                for (i = 0; i < n_landmarks; i++) {
                    val = landmarks[i];
                    port = val[1];
                    if (config.blocked_ports[port]) {
                        log("cannot use port " + port + " for " + val[0]);
                        port = 80;
                    }
                    landmark_data[val[0]] = {
                        'addr': val[0],
                        'port': port,
                        'lat':  val[2],
                        'lon':  val[3],
                        'cbg_m': val[4],
                        'cbg_b': val[5],
                        'rtts': []
                    };
                }
                log("Loaded " + n_landmarks + " landmarks.");
                log("Landmark data: " + JSON.stringify(landmark_data));
            });
    }

    function load_worker() {
        return new Promise(function load_worker_inner (resolve, reject) {
            if (typeof Worker !== "function" &&
                typeof Worker !== "object") {
                reject({ message:
                         "Web Workers are not available. (typeof Worker: " +
                         typeof Worker + ")"
                       });
                return;
            }
            try {
                worker = new Worker(config.static_base + "worker.js");
                worker.onerror = reject;
                worker.onmessage = function load_worker_shim (msg) {
                    try {
                        if (msg.data.status === "ready") {
                            worker.onmessage = worker_message;
                            worker.onerror = worker_error;
                            log("Worker is ready.");
                            resolve();
                        } else if (msg.data.status === "log") {
                            log(msg.data.message);
                        } else {
                            reject(msg.data);
                        }
                    } catch (e) { reject(e); }
                };
            } catch (e) { reject(e); }
        });
    }

    function worker_message (msg) {
        switch (msg.data.status) {
            // The "ready" message is fielded by load_worker_shim, above.
            case "rtt": {
                progress_bar_tick();
                var lm = landmark_data[msg.data.addr];
                lm.rtts.push(msg.data.elapsed);
                if (lm.rtts.length == config.n_probes)
                    place_cbg_circle(lm);
                probe_successes++;
            } break;

            case "rtt_error": {
                log(msg.data.addr + ":" + landmark_data[msg.data.addr].port +
                    ": " + error_to_string(msg.data.error));
                progress_bar_tick();
                probe_failures++;
                if (probe_failures > (config.n_probes*n_landmarks)/10 &&
                    probe_failures > (probe_failures+probe_successes) * .4) {
                    worker_error(new Error("too many probe failures ("
                                           +probe_failures
                                           +"/"+(probe_failures+probe_successes)
                                           +")"));
                }
            } break;

            case "done": {
                log("done (success: " + probe_successes +
                    " failure: " + probe_failures + ")");
                finish_experiment();
            } break;

            case "error": {
                worker_error(msg.data.error);
            } break;

            case "log": {
                log(msg.data.message);
            } break;

            default: {
                worker_error(msg.data);
            } break;
        }
    }

    function run_experiment(ev) {
        hide_id("go");
        show_class("during_run");
        ev.preventDefault();
        progress_bar_begin(n_landmarks * config.n_probes);
        worker.postMessage({ config: config, landmarks: landmark_data });
    }

    /* Reporting */

    // based on http://stackoverflow.com/a/38080051/388520
    function identify_browser() {
        var t, ua = navigator.userAgent, M = ua.match(
                /(opera|chrome|safari|firefox|msie|trident(?=\/))\/?\s*(\d+)/i
        ) || [];
        if (/trident/i.test(M[1])) {
            t = /\brv[ :]+(\d+)/g.exec(ua) || [];
            browser_short_version = 'IE';
            if (t[1])
                browser_short_version += ' ' + t[1];

        } else if (M[1] === 'Chrome' &&
                   (t = ua.match(/\b(OPR|Edge|UBrowser)\/(\d+)/)) !== null) {
            browser_short_version = t[1]
                .replace('OPR', 'Opera')
                .replace('UBrowser', 'UC Browser')
                + ' ' + t[2];

        } else if (M[1] === 'Safari' &&
                   (t = ua.match(/\bVersion\/(\d+)/)) !== null) {
            browser_short_version = 'Safari ' + t[1];

        } else if (M[1] === 'Safari' &&
                   (t = ua.match(/\bAppleWebKit\/(\d+)/)) !== null &&
                   M[2] === t[1]) {
            browser_short_version = 'WebKit ' + t[1];

        } else {
            M = M[2] ? [M[1], M[2]]
                : [navigator.appName, navigator.appVersion, '-?'];
            if ((t = ua.match(/version\/(\d+)/i)) !== null)
                M.splice(1, 1, t[1]);
            browser_short_version = M[0] + ' ' + M[1];
        }

        log("User-Agent: " + ua);
        log("Browser identified as: " + browser_short_version);
        d.getElementById("browser_ver").appendChild(
            d.createTextNode(browser_short_version));
    }

    function radio_value(radioset)
    {
        var i, radios = d.getElementsByName(radioset);
        for (i = 0; i < radios.length; i++)
            if (radios[i].checked)
                return radios[i].value;
        return null;
    }

    function get_report_metadata()
    {
        var md = {
            timestamp: new Date().toISOString(),
            spacing:   config.spacing,
            parallel:  config.parallel,
            timeout:   config.timeout,
            n_probes:  config.n_probes,
            latitude:  +d.getElementById("client-lat").value,
            longitude: +d.getElementById("client-lon").value,
            browser:   browser_short_version
        };
        var proxy_mode = radio_value("proxy-loc");
        if (proxy_mode === "unknown")
            md.proxy_location_unknown = true;
        else if (proxy_mode === "coords") {
            md.proxy_latitude  = +d.getElementById("proxy-lat").value;
            md.proxy_longitude = +d.getElementById("proxy-lon").value;
        }
        if (browser_lat !== null && browser_lon !== null) {
            md.browser_lat = browser_lat;
            md.browser_lon = browser_lon;
        }
        if (geoip_lat !== null && geoip_lon !== null) {
            md.geoip_lat = geoip_lat;
            md.geoip_lon = geoip_lon;
        }
        if (radio_value("publication") === "no")
            md.no_publication = true;

        return md;
    }

    function get_rtt_table()
    {
        var results = [],
            landmarks = Object.keys(landmark_data),
            i, j, lm, addr;
        for (i = 0; i < landmarks.length; i++) {
            addr = landmarks[i];
            lm = landmark_data[addr];
            for (j = 0; j < lm.rtts.length; j++)
                results.push([addr, lm.port, 0, lm.rtts[j]]);
        }
        return results;
    }

    function finish_experiment() {
        hide_class("during_run");
        show_id("after_demo");
    }

    function post_results() {
        var form, blob, md;
        md = get_report_metadata();
        md.results = get_rtt_table();
        blob = JSON.stringify(md);
        log("Will post: " + blob);

        form = new FormData();
        form.append('blob', blob);

        show_id("sending_message");
        hide_id("submit_form");

        fetch(config.results_url, {
            method: "POST",
            body: form
        })
        .then(fetch_maybe_decode_json)
        .then(function (data) {
            var ccode = data["ccode"] || "";
            if (ccode !== "") {
                document.getElementById("ccode-box")
                    .appendChild(document.createTextNode(ccode));
                show_id("ccode-para");
            }
            hide_id("sending_message");
            show_id("completion_message");

        }).catch(function (e) {
            var errorbox = document.getElementById("send_error_message");
            console.error("error posting results:", e);
            log("error posting results: " + error_to_string(e));
            errorbox.innerHTML = "Failed to send results to server.\
 You can see more details in the “technical log” below.";
            hide_id("sending_message");
            show_id("send_error_message");
        });

        // This suppresses "Are you sure you want to reload the page"
        // warning messages in some browsers.
        window.onbeforeunload = function () {};
    }

    /* Master control */

    addGlobalEventListener('error', fatal_error);
    addGlobalEventListener('load', function client_onload() {
        load_timeout = setTimeout(
            function () { fatal_error({message: "Timeout expired."}); },
            60*1000);

        p4       = window.proj4;
        ol       = window.ol;
        wgs      = window.GeographicLib.Geodesic.WGS84;
        fetch    = window.fetch;
        FormData = window.FormData;
        Promise  = window.Promise;
        Worker   = window.Worker;
        geo      = window.navigator ? window.navigator.geolocation : undefined;

        prepare_form();
        identify_browser();

        get_browser_location();
        get_geoip_location();
        Promise.all([
            load_landmarks(),
            load_worker(),
            load_map(),
        ])
            .then(function () {
                if (load_timeout) {
                    clearTimeout(load_timeout);
                    load_timeout = null;
                }
                if (still_ok) {
                    loaded = true;
                    place_pending_pins();
                    show_id("browser_ok");
                    show_id("go");
                }
            })
            .catch(function (err) {
                if (load_timeout) {
                    clearTimeout(load_timeout);
                    load_timeout = null;
                }
                fatal_error(err);
            });
    });
}(window));
