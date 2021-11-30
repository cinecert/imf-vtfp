#!/usr/bin/env python
#
# This file is distributed as part of the IMF Virtual Track Fingerprint proposal
# published at https://github.com/cinecert/imf-vtfp
#
# This program calculates an IMF Virtual Track Fingerprint over the set of
# Resource references defined in the selected virtual track of the given
# IMF composition playlist (CPL.) The resulting identifier is encoded as a URN value.
#

import sys
import hashlib
import re
import struct
import uuid
import xml.etree.ElementTree as ElementTree

cpl_ns_2013 = "http://www.smpte-ra.org/schemas/2067-3/2013"
cpl_ns_2016 = "http://www.smpte-ra.org/schemas/2067-3/2016"
cpl_ns_map = {
    "r1": "http://www.smpte-ra.org/reg/395/2014/13/1/aaf",
    "r2": "http://www.smpte-ra.org/reg/335/2012",
    "r3": "http://www.smpte-ra.org/reg/2003/2012"
    }

def split_tag(tag):
    m = re.match("^\{([^\}]+)\}(\w+)$", tag)
    if not m:
        raise ValueError("Unable to extract namespace name from tag value \"{0}\".".format(tag))
    return m.groups()

def tag_basename(tag):
    return split_tag(tag)[1]

def extract_ns_from_tag(tag):
    return split_tag(tag)[0]

#
class IterableProperties:
    def __init__(self, properties, root=None):
        self.attr_index = 0
        self.attr_names = []
        self.set_attr("ObjectType", self.__class__.__name__)

        for property_item in properties:
            self.set_attr(property_item[0], property_item[1])

        if root is not None:
            self.set_attr("NamespaceName", extract_ns_from_tag(root.tag))
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
#
def parse_uuid(id_value):
    if id_value.find("urn:uuid:") == 0:
        id_value = id_value[9:]
    return uuid.UUID(id_value)
    
#
class Resource(IterableProperties):
    """
    A container for an IMF CPL Resource element, having additional operators
    intended to assist in the calculation of the virtual track thumbprint.
    """
    def __init__(self, root):
        IterableProperties.__init__(
            self, (
                ("EntryPoint", 0),
                ("SourceDuration", 0),
                ("RepeatCount", 1)
                ), root)

        for item in ("EntryPoint", "SourceDuration", "RepeatCount"):
            value = root.find(".//r0:{0}".format(item), cpl_ns_map)
            if value is not None:
                setattr(self, item, int(value.text))

        if self.SourceDuration == 0:
            value = root.find(".//r0:IntrinsicDuration", cpl_ns_map).text
            if value is None:
                raise ValueError("Missing property is required: IntrinsicDuration.")
            self.SourceDuration = int(value)

        self.set_attr("TrackFileId", parse_uuid(root.find(".//r0:TrackFileId", cpl_ns_map).text))

    # Two Clip items shall be determined to be Congruent if they contain the
    # same TrackFileId, EntryPoint, and SourceDuration properties.
    # Congruency determination shall not consider the value of RepeatCount.
    def is_congruent_to(self, rhs):
        return self.TrackFileId == rhs.TrackFileId and \
            self.EntryPoint == rhs.EntryPoint and \
            self.SourceDuration == rhs.SourceDuration

    # A Clip item (the present Clip) shall be determined to be a Continuation
    # of the Clip most recently added to the list
    # (the previous Clip) when the following conditions are true:
    #   * The present Clip is not the first item in the list (i.e., the list is not empty);
    #   * The present Clip item and the previous Clip have identical values of the TrackFileId property;
    #   * Both the present Clip and the previous Clip have a RepeatCount value of 1;
    #   * The index of the first edit unit of the present Clip is exactly one (1) greater
    #     than that of the last edit unit in the previous Clip
    #     (i.e., the regions of the track file idnetified by the two Clips are contiguous.)
    def is_continued_by(self, rhs):
        return self.TrackFileId == rhs.TrackFileId and \
            self.RepeatCount == 1 and \
            rhs.RepeatCount == 1 and \
            self.EntryPoint + self.SourceDuration + 1 == rhs.EntryPoint
    
    # Update the digest withthe canonical encoding of the node properties
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

        self.TrackId = parse_uuid(self.TrackId)

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
    track_id = parse_uuid(track_id)
    node_list = []

    for sequence in cpl.SequenceList:
        if track_id == sequence.TrackId:
            for resource in sequence.ResourceList:
                present = resource
                if node_list:
                    previous = node_list[-1]
                    if previous.is_congruent_to(present):
                        previous.RepeatCount += present.RepeatCount
                        continue

                    elif previous.is_continued_by(present):
                        previous.SourceDuration += present.SourceDuration
                        continue

                node_list.append(present)

    md = hashlib.sha1()
    for node in node_list:
        node.update_digest(md)
                    
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

    ns = extract_ns_from_tag(root.tag)
    if ns not in (cpl_ns_2013, cpl_ns_2016):
        raise ValueError("Document root namespace name is not a valid SMPTE IMF CPL namespace.")
    cpl_ns_map["r0"] = ns
    return root

#
#
if __name__ == "__main__":
    # Print the thumbprint value of the given virtual track
    # Option "-n" sets the length of the hexadecimal digest string, default: 10 characters
    if len(sys.argv) == 5 and sys.argv[2] == "-n":
        root = setup_cpl_document(sys.argv[1])
        n = max(2, min(int(sys.argv[3]), 40))
        vtfp = create_imf_vtfp_for_track(root, sys.argv[4])
        print(format_imf_vtfp_urn(vtfp, n))

    # Print the thumbprint value of the given virtual track
    elif len(sys.argv) == 3:
        root = setup_cpl_document(sys.argv[1])
        vtfp = create_imf_vtfp_for_track(root, sys.argv[2])
        print(format_imf_vtfp_urn(vtfp))

    # List the virtual tracks in the CPL
    elif len(sys.argv) == 2:
        root = setup_cpl_document(sys.argv[1])
        for item in list_imf_cpl_tracks(root):
            print(item)

    else:
        raise RuntimeError("USAGE: imf_vtfp.py <cpl-filename> [-n <n>] [<track-id>]")


#
# end imf_vtfp.py
#
