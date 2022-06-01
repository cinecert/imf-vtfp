#!/usr/bin/env python
#
# This file is distributed as part of the IMF Virtual Track Fingerprint proposal
# published at https://github.com/cinecert/imf-vtfp
#
# This program calculates an IMF Virtual Track Fingerprint over the set of
# Resource references defined in the selected virtual track of the given
# IMF composition playlist (CPL.) The resulting identifier is encoded as a URN value.
#
# Copyright 2022 CineCert Inc. See /LICENSE.md for terms.
#

import sys
import hashlib
import re
import struct
import uuid
import argparse
import xml.etree.ElementTree as ElementTree

CT_urn_uuid = "urn:uuid:"
CT_EntryPoint = "EntryPoint"
CT_SourceDuration = "SourceDuration"
CT_RepeatCount = "RepeatCount"
CT_TrackFileId = "TrackFileId"

# XML namespace names used in IMF CPL documents
cpl_ns_2013 = "http://www.smpte-ra.org/schemas/2067-3/2013"
cpl_ns_2016 = "http://www.smpte-ra.org/schemas/2067-3/2016"
cpl_ns_map = {
    "r0": None, # to be selected at runtime
    "r1": "http://www.smpte-ra.org/reg/395/2014/13/1/aaf",
    "r2": "http://www.smpte-ra.org/reg/335/2012",
    "r3": "http://www.smpte-ra.org/reg/2003/2012"
    }

# convert string UUID (with optional URM prefix) to uuid.UUID object
def parse_uuid(id_value):
    if id_value.find(CT_urn_uuid) == 0:
        id_value = id_value[9:]
    return uuid.UUID(id_value)

#
# XML parsing helper: split ElementTree "{ns}tag" into tuple (ns, tag)
def split_tag(tag):
    m = re.match("^\{([^\}]+)\}(\w+)$", tag)
    if not m:
        raise ValueError("Unable to extract namespace name from tag value \"{0}\".".format(tag))
    return m.groups()

def tag_basename(tag):
    return split_tag(tag)[1]

def tag_ns(tag):
    return split_tag(tag)[0]

#
class IterableProperties:
    """A base class for interrogable property classes."""
    def __init__(self, properties, root=None):
        self.attr_index = 0
        self.attr_names = []
        self.set_attr("ObjectType", self.__class__.__name__)

        for property_item in properties:
            self.set_attr(property_item[0], property_item[1])

        if root is not None:
            self.set_attr("NamespaceName", tag_ns(root.tag))
            self.set_attr("TagName", tag_basename(root.tag))

            for child in root:
                name = tag_basename(child.tag)
                value = getattr(self, name, None)
                if value is None:
                    self.set_attr(name, child.text)

    #
    def set_attr(self, name, value):
        setattr(self, name, value)
        if name not in self.attr_names:
            self.attr_names.append(name)

    #
    def __iter__(self):
        while True:
            try:
                name = self.attr_names[self.attr_index]
                self.attr_index += 1
                yield name, getattr(self, name)
            except:
                self.attr_index = 0
                raise StopIteration()

    
#
class Resource(IterableProperties):
    """
    A container for an IMF CPL Resource element, having additional operators
    intended to assist in the calculation of the virtual track fingerprint.
    """
    def __init__(self, root=None):
        IterableProperties.__init__(
            self, (
                (CT_EntryPoint, 0),
                (CT_SourceDuration, 0),
                (CT_RepeatCount, 1)
                ), root)

        if root is not None:
            for item in (CT_EntryPoint, CT_SourceDuration, CT_RepeatCount):
                value = root.find(".//r0:{0}".format(item), cpl_ns_map)
                if value is not None:
                    setattr(self, item, int(value.text))

            if self.SourceDuration == 0:
                value = root.find(".//r0:IntrinsicDuration", cpl_ns_map).text
                if value is None:
                    raise ValueError("Missing property IntrinsicDuration is required.")

                self.SourceDuration = int(value)

            self.set_attr(CT_TrackFileId, parse_uuid(root.find(".//r0:"+CT_TrackFileId, cpl_ns_map).text))

    #
    def copy(self):
        copy = Resource()
        for item in (CT_TrackFileId, CT_EntryPoint, CT_SourceDuration, CT_RepeatCount):
            setattr(copy, item, getattr(self, item))
        return copy

    # Congruency from one Resource to its successor is detected when
    # both items have the same TrackFileId, EntryPoint, and SourceDuration properties.
    # Congruency determination shall not consider the value of RepeatCount.
    def is_congruent_with(lhs, rhs):
        return lhs.TrackFileId == rhs.TrackFileId and \
            lhs.EntryPoint == rhs.EntryPoint and \
            lhs.SourceDuration == rhs.SourceDuration

    # Continuity from one Resource to its successor is detected when:
    # (a) the right-hand Resource and left-hand Resource have equal TrackFileId, and
    # (b) lhs.RepeatCount and rhs.RepeatCount are 1 (one), and
    # (c) The first Edit Unit of the right-hand Resource is exactly one (1) greater
    #     than the last Edit Unit of the left-hand Resource.
    def is_continued_by(lhs, rhs):
        return lhs.TrackFileId == rhs.TrackFileId and \
            lhs.RepeatCount == 1 and \
            rhs.RepeatCount == 1 and \
            lhs.EntryPoint + lhs.SourceDuration == rhs.EntryPoint

    # Update the digest with the canonical encoding of the node properties
    def update_digest(self, digest):
        digest.update(self.TrackFileId.bytes)
        digest.update(struct.pack(">Q", self.EntryPoint))
        digest.update(struct.pack(">Q", self.SourceDuration))
        digest.update(struct.pack(">Q", self.RepeatCount))

#
class Sequence(IterableProperties):
    """
    A container for an IMF CPL Sequence element.
    """
    def __init__(self, root):
        IterableProperties.__init__(
            self, (
                ("TrackId", None),
                ("ResourceList", [])
                ), root)

        # Gather the Resource elements annd create Resource items.
        for item in root.findall(".//r0:Resource", cpl_ns_map):
            self.ResourceList.append(Resource(item))

            self.TrackId = parse_uuid(str(self.TrackId))

#
class CompositionPlaylist(IterableProperties):
    """
    A container for an IMF CPL CompositionPlaylist element.
    """
    def __init__(self, root):
        IterableProperties.__init__(
            self, (
                ("Id", None),
                ("SequenceList", [])
                ), root)

        # Gather the Sequence elements, ignore Segment boundaries.
        # Create Sequence items.
        for item in root.findall(".//r0:SequenceList", cpl_ns_map):
            for seq_item in item.getchildren():
                self.SequenceList.append(Sequence(seq_item))

#
def create_imf_vtfp_for_track(root, track_id):
    """
    Return the sha1 message digest over the set of Resources
    found in virtual track <track_id> in the CPL document.
    """
    cpl = CompositionPlaylist(root)
    md = hashlib.sha1()
    previous = None

    for sequence in cpl.SequenceList:
        if track_id == sequence.TrackId:
            for resource in sequence.ResourceList:
                if previous is None:
                    # must be a separate instance so that SourceDuration
                    # can be altered without side-effect
                    previous = resource.copy()
                else:
                    if previous.is_congruent_with(resource):
                        previous.RepeatCount += resource.RepeatCount

                    elif previous.is_continued_by(resource):
                        previous.SourceDuration += resource.SourceDuration

                    else:
                        previous.update_digest(md)
                        previous = resource.copy()

    if previous is None:
        raise RuntimeError("No such virtual track: \"{0}\".".format(track_id))

    previous.update_digest(md)
    return md.digest()

#
def list_imf_cpl_tracks(root):
    """
    Return the set of TrackId values found in CPL document.
    """
    cpl = CompositionPlaylist(root)
    track_ids = set()

    for sequence in cpl.SequenceList:
        track_ids.add(" ".join((str(sequence.TrackId), sequence.TagName)))

    return track_ids

#
def format_imf_vtfp_urn(raw_digest, n=10):
    """
    Format a digest value as an IMF-VTFP URN.
    """
    return "urn:smpte:imf-vtfp:" + raw_digest.encode("hex")[:n]


#
def setup_cpl_document(filename):
    tree = ElementTree.parse(filename)
    assert(tree is not None)
    root = tree.getroot()

    ns = tag_ns(root.tag)
    if ns not in (cpl_ns_2013, cpl_ns_2016):
        raise ValueError("Document root namespace name is not a valid SMPTE IMF CPL namespace.")

    cpl_ns_map["r0"] = ns
    return root

#
def main(options):
    if not options.track_id:
        # List the virtual tracks in the CPL
        root = setup_cpl_document(options.cpl_filename)
        assert("r0" in cpl_ns_map)
        for item in list_imf_cpl_tracks(root):
            print(CT_urn_uuid+item)

    else:
        # Print the fingerprint value of the given virtual track
        root = setup_cpl_document(options.cpl_filename)
        track_id = parse_uuid(options.track_id)
        assert("r0" in cpl_ns_map)
        vtfp = create_imf_vtfp_for_track(root, track_id)
        print(format_imf_vtfp_urn(vtfp, options.width))


#
def setup_parser(parser):
    parser.add_argument(
        "cpl_filename", metavar="cpl-filename")

    parser.add_argument(
        "track_id", metavar="track-id", nargs="?")

    parser.add_argument(
        "-w", "--width", dest="width", action="store", metavar="n", type=int, default=8,
        help="Number of hex digits in the identifier.")

    parser.add_argument(
        "--with-stack-trace", action="store_true", dest="with_stack_trace", default=False,
        help="Display a Python stack trace when an error occurs")

#
#
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        version = "0.2",
        usage = "imf_vtfp.py <cpl-filename> [-n <n>] [<track-id>]",
        description = "Calculate IMF Virtual Track Fingerprint"
    )

    setup_parser(parser)
    options = parser.parse_args()

    if options.with_stack_trace:
        main(options)
    else:
        try:
            main(options)

        except KeyboardInterrupt:
            sys.stderr.write('\nProgram interrupted.\n')
            sys.exit(2)

        except Exception as e:
            sys.stderr.write(str(e) + '\n')
            sys.stderr.write('Program stopped on error.\n')
            sys.exit(1)


#
# end imf_vtfp.py
#
