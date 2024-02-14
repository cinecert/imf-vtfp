#!/usr/bin/env python3
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
CT_LeftEye = "LeftEye"
CT_RightEye = "RightEye"
CT_TrackFileResourceType = "TrackFileResourceType"
CT_StereoImageTrackFileResourceType = "StereoImageTrackFileResourceType"

# XML namespace names used in IMF CPL documents
cpl_ns_2013 = "http://www.smpte-ra.org/schemas/2067-3/2013"
cpl_core_ns_2013 = "http://www.smpte-ra.org/schemas/2067-2/2013"
cpl_ns_2016 = "http://www.smpte-ra.org/schemas/2067-3/2016"
cpl_core_ns_2016 = "http://www.smpte-ra.org/schemas/2067-2/2016"
cpl_ns_map = {
    "r0": None,  # to be selected at runtime
    "r1": None,  # to be selected at runtime
    "r2": "http://www.smpte-ra.org/reg/395/2014/13/1/aaf",
    "r3": "http://www.smpte-ra.org/reg/335/2012",
    "r4": "http://www.smpte-ra.org/reg/2003/2012"
    }
core_ns_map = {
    cpl_ns_2013: cpl_core_ns_2013,
    cpl_ns_2016: cpl_core_ns_2016
    }

# convert string UUID (with optional URM prefix) to uuid.UUID object
def parse_uuid(id_value):
    if id_value.find(CT_urn_uuid) == 0:
        id_value = id_value[9:]
    return uuid.UUID(id_value)

#
# XML parsing helper: split ElementTree "{ns}tag" into tuple (ns, tag)
def split_tag(tag):
    m = re.match("^\{([^}]+)}(\w+)$", tag)
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

        self.ResourceType = CT_TrackFileResourceType

        if root is not None:
            if root.attrib:
                self.ResourceType = root.attrib['{http://www.w3.org/2001/XMLSchema-instance}type']
            n = self.ResourceType.find(':')
            if n != -1:
                self.ResourceType = self.ResourceType[n+1:]

            for item in (CT_EntryPoint, CT_SourceDuration, CT_RepeatCount):
                value = root.find(".//r0:"+item, cpl_ns_map)
                if value is not None:
                    setattr(self, item, int(value.text))

            if self.SourceDuration == 0:
                value = root.find(".//r0:IntrinsicDuration", cpl_ns_map).text
                if value is None:
                    raise ValueError("Required property IntrinsicDuration is missing.")

                self.SourceDuration = int(value)

            if self.ResourceType == CT_StereoImageTrackFileResourceType:
                le = root.find(".//r1:"+CT_LeftEye, cpl_ns_map)
                re = root.find(".//r1:"+CT_RightEye, cpl_ns_map)
                if le is None or re is None:
                    raise ValueError("Malformed stereo image resource.")

                self.left_eye = Resource(le)
                self.right_eye = Resource(re)
                self.TrackFileId = None

                if self.left_eye.EditRate != self.right_eye.EditRate:
                    raise ValueError("Left/Right EditRate mismatch.")

                if self.SourceDuration != self.left_eye.SourceDuration:
                    raise ValueError("SourceDuration mismatch.")

                if self.left_eye.SourceDuration != self.right_eye.SourceDuration:
                    raise ValueError("Left/Right SourceDuration mismatch.")

                if self.left_eye.RepeatCount != 1 or self.right_eye.RepeatCount != 1:
                    raise ValueError("Left/Right RepeatCount invalid.")
            else:
                self.set_attr(CT_TrackFileId, parse_uuid(root.find(".//r0:"+CT_TrackFileId, cpl_ns_map).text))


    #
    def copy(self):
        copy = Resource()
        copy.ResourceType = self.ResourceType
        for item in (CT_TrackFileId, CT_EntryPoint, CT_SourceDuration, CT_RepeatCount):
            setattr(copy, item, getattr(self, item))

        if self.ResourceType == CT_StereoImageTrackFileResourceType:
            copy.left_eye = self.left_eye
            copy.right_eye = self.right_eye

        return copy

    #
    def extend_repeat(self, rhs):
        if self.ResourceType == CT_StereoImageTrackFileResourceType:
            self.left_eye.extend_repeat(rhs.left_eye)
            self.right_eye.extend_repeat(rhs.right_eye)
        else:
            self.RepeatCount += rhs.RepeatCount

    #
    def extend_source_duration(self, rhs):
        if self.ResourceType == CT_StereoImageTrackFileResourceType:
            self.left_eye.extend_source_duration(rhs.left_eye)
            self.right_eye.extend_source_duration(rhs.right_eye)
        else:
            self.SourceDuration += rhs.SourceDuration

    # Congruency from one Resource to its successor is detected when
    # both items have the same TrackFileId, EntryPoint, and SourceDuration properties.
    # Congruency determination shall not consider the value of RepeatCount.
    def is_congruent_with(lhs, rhs):
        if lhs.ResourceType == CT_StereoImageTrackFileResourceType:
            return lhs.left_eye.is_congruent_with(rhs.left_eye) and \
                lhs.right_eye.is_congruent_with(rhs.right_eye)

        return lhs.TrackFileId == rhs.TrackFileId and \
            lhs.EntryPoint == rhs.EntryPoint and \
            lhs.SourceDuration == rhs.SourceDuration

    # Continuity from one Resource to its successor is detected when:
    # (a) the right-hand Resource and left-hand Resource have equal TrackFileId, and
    # (b) lhs.RepeatCount and rhs.RepeatCount are 1 (one), and
    # (c) The first Edit Unit of the right-hand Resource is exactly one (1) greater
    #     than the last Edit Unit of the left-hand Resource.
    def is_continued_by(lhs, rhs):
        if lhs.ResourceType == CT_StereoImageTrackFileResourceType:
            return lhs.left_eye.is_continued_by(rhs.left_eye) and \
                lhs.right_eye.is_continued_by(rhs.right_eye)

        return lhs.TrackFileId == rhs.TrackFileId and \
            lhs.RepeatCount == 1 and \
            rhs.RepeatCount == 1 and \
            lhs.EntryPoint + lhs.SourceDuration == rhs.EntryPoint

    # Update the digest with the canonical encoding of the node properties
    def update_digest(self, digest):
        if self.ResourceType == CT_StereoImageTrackFileResourceType:
            digest.update(struct.pack(">Q", self.SourceDuration))
            digest.update(struct.pack(">Q", self.RepeatCount))
            digest.update(self.left_eye.TrackFileId.bytes)
            digest.update(struct.pack(">Q", self.left_eye.EntryPoint))
            digest.update(self.right_eye.TrackFileId.bytes)
            digest.update(struct.pack(">Q", self.right_eye.EntryPoint))
        else:
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
            for seq_item in list(item):
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
                        previous.extend_repeat(resource)

                    elif previous.is_continued_by(resource):
                        previous.extend_source_duration(resource)

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
    return "urn:smpte:imf-vtfp:" + raw_digest.hex()[:n]

#
def setup_cpl_document(filename):
    tree = ElementTree.parse(filename)
    assert(tree is not None)
    root = tree.getroot()

    ns = tag_ns(root.tag)
    if ns not in (cpl_ns_2013, cpl_ns_2016):
        raise ValueError("Document root namespace name is not a valid SMPTE IMF CPL namespace.")

    cpl_ns_map["r0"] = ns
    cpl_ns_map["r1"] = core_ns_map[ns]
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
        # print(vtfp)
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
