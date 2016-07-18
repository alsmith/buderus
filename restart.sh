#!/bin/sh
cd `dirname $0`

if [ -f server.pid ]
then
  kill -15 `cat server.pid`
fi
./server.py $*

