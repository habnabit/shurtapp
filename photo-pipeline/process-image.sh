#!/bin/sh
set -eu
dataloc=$(dirname "$0")
convert -auto-orient -resize 307200@ "$1" jpg:- \
    | jpegtran -copy none -optimize -scans "$dataloc/jpeg_scan_rgb.txt" >"$2"
