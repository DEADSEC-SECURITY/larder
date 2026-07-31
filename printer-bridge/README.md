# Larder printer bridge

The bridge runs beside the USB-connected MUNBYN RW405B, polls Larder's durable
print queue, and writes 2.25 x 1.25-inch 203-DPI TSPL labels directly to the
Linux USB printer character device. It does not require CUPS or a Linux vendor
driver.

Printed job IDs are committed to a local SQLite ledger before the bridge
acknowledges the central job. If the API is unavailable after printing, the
same job is acknowledged later without printing a duplicate label.
