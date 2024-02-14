#!/bin/sh -e
#
#!/usr/bin/env python
#
# This file is distributed as part of the IMF Virtual Track Fingerprint proposal
# published at https://github.com/cinecert/imf-vtfp
#
# This program is a test harness for the Virtual Track Fingerprint project
#
# Copyright 2022 CineCert Inc. See /LICENSE.md for terms.
#

#
# At the time of this commit, these are the expected input files (<SHA256> <filename>):
# 25266f5d3e63d9c362b65a7d0fb3dc2676fa2404d3d4d8b9679474916984388d  vt-fp-test-1.cpl.xml
# 51a7b6bfef12332d2f803733b7acf32cc402c7152e4f9d8ffee7784a68f56452  vt-fp-test-2.cpl.xml
# b7657b59217477268c3d71110121afcc0bd50654122a8844e2c0691afb8d1cd6  vt-fp-test-3.cpl.xml
# b3b99b15a34f0cf4d3f49647830aa1b822bee0e33b01d5081ae5366fc95ee46d  vt-fp-test-4.cpl.xml
# 92663cc2a84cf1c0e88d6d24a2b55bb29729e38bbbaf1c5862377f2d73477811  vt-fp-test-5.cpl.xml


for item in 1 2 3 4 5
do
    filename="vt-fp-test-${item}.cpl.xml"
    vt_id=`../imf_vtfp.py ${filename} | grep MainImageSequence | head -1 | cut -f1 -d' '`
    test_id=`../imf_vtfp.py --with-stack -w40 ${filename} ${vt_id}`
    match_id=`grep Annotation ${filename}| sed 's/<\/Annotation>//' | sed 's/<Annotation>//'`

    if [ ${match_id} = ${test_id} ]
    then
	echo "${filename} OK"
    else
        echo "Got ${test_id}, expecting ${match_id}"
	echo "${filename} FAILED"
    fi
done

#
# end test.sh
#
    
