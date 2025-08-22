# brother_ql_web

This is a web service to print labels on Brother QL label printers.

You need Python 3 or Docker for this software to work.

The web interface is [responsive](https://en.wikipedia.org/wiki/Responsive_web_design).
There's also a screenshot showing [how it looks on a smartphone](./screenshots/Label-Designer_Phone.png)

## Fork info

There are a lot of forks of the `brother_ql` and `brother_ql_web` repos from [pklaus](https://github.com/pklaus/brother_ql). I tried to cherry pick a fairly recent and well maintainable state by using [matmair/brother_ql-inventree](https://github.com/matmair/brother_ql-inventree) as a dependency for communicating with the printers and [tbnobody/brother_ql_web](https://github.com/tbnobody/brother_ql_web) as a base for the frontend as there have been a few fixes and improvements implemented over there.

For now I have added Docker support and the ability to print red images on supported paper/printers.

![Screenshot](./screenshots/Label-Designer_Desktop.png)

### Additional Features

-   Print text as QR Code
    -   Add text to QR Code
    -   Change size of QR Code
-   Upload files to print
    -   .pdf, .png and .jpg files
    -   automatically convertion to black/white image
-   Change print color for black/white/red labels
-   Print lables multiple times
    -   Cut every label
    -   Cut only after the last label
-   Migrated GUI to Bootstrap 4
-   Make preview for round labels.. round
-   Print images on red/black paper
-   Dockerized

### Docker Compose

You may also use the example [`docker-compose.yml`](./docker-compose.yml) file provided in this repository to quickly get started with Docker Compose:

``` yaml
services:
  brother_ql_web:
    image: davidramiro/brother-ql-web:latest # latest online version
    # build: . # building locally from source (see git clone below)
    container_name: brother_ql_web
    restart: always
    ports:
      - "8013:8013"
    devices:
      - "/dev/usb/lp0:/dev/usb/lp0"
    command: >
      --model QL-800
      --default-label-size 62
      file:///dev/usb/lp0
```

### Run via Docker

You can pull the image from `davidramiro/brother-ql-web` on Docker Hub.
You have to pass your printer model as `--model` argument. At the end of the arguments you have to add your device socket (linux kernel backend), USB identifier (pyusb backend) or network address (TCP).
Please note you might have to pass your device to the container via the `--device` flag.

Example command to start the application, connecting to a QL-800 on `/dev/usb/lp0`, setting label size to 62mm:

```bash
docker run -d \
    --restart=always \
    --name=brother-ql-web \
    -p 8013:8013 \
    --device=/dev/usb/lp0 \
    davidramiro/brother-ql-web:latest \
    --default-label-size 62 \
    --model QL-800 \
    file:///dev/usb/lp0
```

To build the image locally:

```bash
git clone https://github.com/davidramiro/brother_ql_web.git
cd brother_ql_web
docker buildx build -t brother-ql-web .

# alternatively, if buildx is not available
docker build -t brother-ql-web .
```

### Usage

Once it's running, access the web interface by opening the page with your browser.
If you run it on your local machine, go to <http://localhost:8013>.
You will then be forwarded by default to the interactive web gui located at `/labeldesigner`.

All in all, the web server offers:

-   a Web GUI allowing you to print your labels at `/labeldesigner`,
-   an API at `/api/print/text?text=Your_Text&font_size=100&font_family=Minion%20Pro%20(%20Semibold%20)`
    to print a label containing 'Your Text' with the specified font properties.

### License

This software is published under the terms of the GPLv3, see the LICENSE file in the repository.

Parts of this package are redistributed software products from 3rd parties. They are subject to different licenses:

-   [Bootstrap](https://github.com/twbs/bootstrap), MIT License
-   [Font Awesome](https://github.com/FortAwesome/Font-Awesome), CC BY 4.0 License
-   [jQuery](https://github.com/jquery/jquery), MIT License
