# active-geolocator

This is a testbed implementation of various “active geolocation”
algorithms.  Please see the [website][] for an overview of the
project.

## Running the measurement client

If you came here _from_ the website, you’re probably interested in
running the measurement client, which is in the
[`measurement-client`][measurement-client] subdirectory of this
repository.

You should only run the client if you are willing to tell us the
latitude and longitude of the computer you’re running it on, to at
least 1/100 of a degree (roughly half an arcminute, or 1km of position
uncertainty, depending on the latitude).  The best way to determine
the position with this accuracy is using [GPS][].  Most smartphones
can take a GPS reading.  The iPhone ships with a “Compass” utility
that, among other things, shows you your latitude and longitude (in
degrees, minutes, and seconds; you will have to convert). For Android,
you need a third-party app: we suggest “[My GPS Coordinates][mygps].”

If you don’t have a GPS-capable phone or dedicated receiver, or you
can’t go to where the computer is and take a GPS reading, the next
best option is to look up the postal address of the building, using
one of the several conversion services available online (e.g. [1][c1],
[2][c2], [3][c3]).  In this case, please make sure to feed the
latitude and longitude back into a mapping service and verify that
it’s accurate.

_Don’t_ look your IP address up in a geolocation service, because one
of the goals of this project is to independently audit the accuracy of
those services.

The measurement software has two components, one written in Python
and the other in C. The Python component has no dependencies outside
the standard library, and is known to work with versions 2.7, 3.4,
and 3.5 of the interpreter. The C component is self-contained (not a
Python module), depends only on standard ISO C and POSIX interfaces,
and is known to work on recent versions of Linux, FreeBSD, and OSX. It
should work on any modern Unix.  (If you know how to time TCP
handshakes with high accuracy on Windows, we’d be glad to take your
patches.)

Because of the C component, running the client from the command line
is a three-step procedure.  Starting from a Git checkout of this
repository:

    $ cd measurement-client
    $ ./configure
    $ make
    $ ./probe --latitude=<LATITUDE> --longitude=<LONGITUDE>

where `<LATITUDE>` and `<LONGITUDE>` are the latitude and longitude
you looked up earlier, as decimal degrees. Use negative numbers for
south of the equator / west of Greenwich, and remember to round off
the numbers to the precision you are comfortable with. If you don’t
want the data you submit to be included in any future publication of a
redacted version of our database, append `--no-publication` to the
`probe` command.

The final `probe` command can take as much as an hour to run, but 5 to
20 minutes is more typical.  It reports its progress once a minute.
The results are automatically uploaded to the project website, and are
also written to a file `probe-result-YYYY-MM-DD-N.json`.

By running `probe`, you assert that you are age 18 or older, and you
consent to the collection of your computer’s IP address, its latitude
and longitude as reported by you, and the time it takes network
messages to reach roughly 200 other computers and return. You
understand and agree that Carnegie Mellon is legally required to
preserve all research data for at least three years, and could be
required to disclose your location and IP address by a future law,
regulation, subpoena or court order.

If any of the above steps fail, please [file an issue][].  We will
need to see the unedited, complete output of the above commands up to
the point where they failed, and we will also need to know which
operating system and compiler you are using.  It’s helpful if you
attach the file `config.log` to the issue (you may have to rename it
`config.txt` first, because Github).  Note: to maintain the
confidentiality of your IP address and location, do _not_
attach any `probe-result-YYYY-MM-DD-N.json` files to Github issues.

If you have access to computers in several cities, please do run the
software on all of them. It’s not as helpful to run it on more than one
computer in the _same_ city, unless their routes to the Internet
backbone are very different (for instance, if one gets its connectivity
from a residential ISP, and another from a cellphone system).

If you have access to VPN or SOCKSv5 proxies, and you can find out the
accurate location of the proxy _as well as_ the client host, please do
run the measurement through the proxy. For VPNs, activate the VPN as
the default route, then do:

    $ ./probe --latitude=<CLIENT LAT> --longitude=<CLIENT LONG> \
              --proxy-latitude=<PROXY LAT> --proxy-longitude=<PROXY LONG>

For SOCKS you must explicitly state the proxy’s address.
Authentication is not supported.

    $ ./probe --latitude=<CLIENT LAT> --longitude=<CLIENT LONG> \
              --proxy-latitude=<PROXY LAT> --proxy-longitude=<PROXY LONG> \
              --socks5 <HOST>:<PORT>

## What does the measurement client do?

It downloads a list of IP addresses from this website, and then it
makes several TCP connections to each address, and measures the time
for the connection to resolve.  (If a connection succeeds, it is
immediately closed; no data is sent or received.)  It randomizes the
list and spaces out the connections in time, to minimize impact on the
remote peers.

Once all the measurements are made, the results are transmitted back
to this website along with your computer’s IP address and reported
location.  If you took measurements via a proxy, we don’t learn your
computer’s IP address but we do learn the proxy’s, and we learn both
computers’ physical locations.

All of the submitted data is stored either encrypted or on computers
under our physical control.  We may publish the data set in the
future; if we do, it will include your computer’s location and AS
number but not its IP address.  If you don’t want the data you submit
to be included in such a publication, append `--no-publication` to the
`probe` command.

[website]: https://hacks.owlfolio.org/active-geo/
[measurement-client]: https://github.com/zackw/active-geolocator/tree/master/measurement-client
[GPS]: https://en.wikipedia.org/wiki/Global_Positioning_System
[mygps]: https://play.google.com/store/apps/details?id=com.gpscoordinatesandlocation
[c1]: http://stevemorse.org/jcal/latlon.php
[c2]: http://www.latlong.net/convert-address-to-lat-long.html
[c3]: http://www.gps-coordinates.net/
[file an issue]: https://github.com/zackw/active-geolocator/issues/new
