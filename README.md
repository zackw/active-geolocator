# active-geolocator

This is a testbed implementation of various "active geolocation"
algorithms.  Please see the [website][] for an overview of the
project.

## Running the measurement client

If you came here _from_ the website, you're probably interested in
running the measurement client, which is in the
[`measurement-client`][measurement-client] subdirectory of this
repository.

It's very important that you only run the client if you are willing to
tell me the latitude and longitude of the computer you're running it
on, to at least 1/100 of a degree (roughly half an arcminute, or 1km
of position uncertainty).  _Don't_ look your IP address up in a
geolocation service to get this information, because one of the goals
of this project is to independently audit the accuracy of those
services.  Looking up the postal address is usually OK, as long as the
post office delivers mail directly to your building.  Locations
measured by a GPS receiver (such as most smartphones nowadays) are
better.

The client has two components, one written in [Python][], the other in
C.  The C component is self-contained (not a Python module) and is
known to work on recent versions of Linux, FreeBSD, and OSX; it
_should_ work on basically any modern Unix.  (If you know how to time
TCP handshakes with high accuracy on Windows, we'd be glad to take
your patches.)  The Python component has no system dependencies and is
known to work with Python 2.7, 3.4, and 3.5.

Because of the C component, running the client from the command line
is a three-step procedure.  Starting from a Git checkout of this
repository:

    $ cd measurement-client
    $ ./configure
    $ make
    $ ./probe --latitude=<LATITUDE> --longitude=<LONGITUDE>

The final `probe` command can take two or three hours to run.  It
reports its progress once a minute.  The results are automatically
uploaded to the project website, and are also written to a file
`probe-result-YYYY-MM-DD-N.json`.

`<LATITUDE>` and `<LONGITUDE>` should be in decimal degrees; use
negative numbers for south of the equator / west of Greenwich.

The client will work if run through a VPN proxy or a SOCKS proxy, and
we're very interested in measurements taken that way; however, you
need to specify the proxy's latitude and longitude _as well as_ the
client computer's:

    $ ./probe --latitude=<CLIENT LAT> --longitude=<CLIENT LONG> \
              --proxy-latitude=<PROXY LAT> --proxy-longitude=<PROXY LONG>

If you're using a SOCKS proxy, you have to specify its address on the
command line:

    $ ./probe --latitude=<CLIENT LAT> --longitude=<CLIENT LONG> \
              --proxy-latitude=<PROXY LAT> --proxy-longitude=<PROXY LONG> \
              --socks5 <HOST>:<PORT>

Again, _don't_ look the proxy's IP address up in a geolocation
service, and _don't_ do this unless you are willing to provide
accurate information!

If any of these steps fail, please [file an issue][].  I will need to
see the _unedited_ complete output of the above commands up to the
point where they failed, and I will also need to know which operating
system and compiler you are using.  It's helpful if you attach the
file `config.log` to the issue (you may have to rename it
`config.log.txt` first, because Github).

## What does the measurement client do?

It downloads a list of IP addresses from this website, and then it
makes several TCP connections to each address, and measures the time
for the connection to resolve.  (If a connection succeeds, it is
immediately closed; no data is sent or received.)  It randomizes the
list and spaces out the connections in time, to minimize impact on the
remote peers.

Once all the measurements are made, the results are transmitted back
to this website along with your computer's IP address and reported
location.  If you took measurements via a proxy, we don't learn your
computer's IP address but we do learn the proxy's, and we learn both
computers' physical locations.

All of the submitted data is stored either encrypted or on computers
under our physical control.  We may publish the data set in the
future; if we do, it will include your computer's location and AS
number but not its IP address.


[website]: https://hacks.owlfolio.org/active-geo/
[measurement-client]: https://github.com/zackw/active-geolocator/tree/master/measurement-client
[Python]: https://www.python.org/
[file an issue]: https://github.com/zackw/active-geolocator/issues/new
