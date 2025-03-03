# -*- coding: utf-8 -*-
import django
django.setup()

import re
import csv
import requests
from io import StringIO
from collections import defaultdict

from sefaria.system.database import db
from sefaria.model import *
from sefaria.system.exceptions import DuplicateRecordError
from sefaria.model.abstract import AbstractMongoRecord

"""
0 key
1 'Primary English Name'
2 'Secondary English Names'
3 'Primary Hebrew Name'
4 'Secondary Hebrew Names'
5 'Birth Year '
6 'Birth Place'
7 'Death Year'
8 'Death Place'
9 'Halachic Era'
10'English Biography'
11'Hebrew Biography'
12'English Wikipedia Link'
13'Hebrew Wikipedia Link'
14'Jewish Encyclopedia Link'
...
24 'Sex'"
"""

eras = {
    "Gaonim": "GN",
    "Rishonim": "RI",
    "Achronim": "AH",
    "Contemporary": "CO"
}
era_slug_map = {
    "GN": "geon-person",
    "RI": "rishon-person",
    "AH": "achron-person",
    "CO": "modern-person",
    "KG": "mishnaic-people",
    "PT": "mishnaic-people",
    "T": "mishnaic-people",
    "A": "talmudic-people",
}

isa_object_aggregate_map = {
    'tosafot': 'group-of-rishon-people'
}

url = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vSx60DLNs8Dp0l2xpsPjrxD3dBpIKASXSBiE-zjq74SvUIc-hD-mHwCxsuJpQYNVHIh7FDBwx7Pp9zR/pub?gid=0&single=true&output=csv'
response = requests.get(url)
data = response.content.decode("utf-8")
cr = csv.reader(StringIO(data))
rows = list(cr)[4:]

# Validate every slug is unique and doesn't exist as a non-author
internal_slug_count = defaultdict(int)
has_slug_issues = False
for l in rows:
    slug = l[0].encode('utf8').decode()
    primary_title = l[1].strip() if len(l[1].strip()) > 0 else l[3].strip()
    if re.search(fr'^{re.escape(AbstractMongoRecord.normalize_slug(primary_title))}\d*$', slug) is None:
        print(f"ERROR: slug '{slug}' does not match primary title '{primary_title}'. Expected slug '{AbstractMongoRecord.normalize_slug(primary_title)}'")
        has_slug_issues = True
    if len(l[9]) == 0:
        print(f"ERROR: slug '{slug}' must have column 'Halachic Era' filled in.")
        has_slug_issues = True
    if len(slug.strip()) == 0: continue
    internal_slug_count[slug] += 1
for slug, count in internal_slug_count.items():
    if count > 1:
        print(f"ERROR: slug {slug} appears {count} times on this sheet. Please update slug in sheet to be internally unique")
        has_slug_issues = True
    non_author = Topic().load({"slug": slug, "subclass": {"$ne": "author"}})
    if non_author is not None:
        print(f"ERROR: slug {slug} exists as a non-author. Please update slug in sheet to be globally unique.")
        has_slug_issues = True
    if AbstractMongoRecord.normalize_slug(slug) != slug:
        print(f"ERROR: slug '{slug}' does not match slugified version which is '{AbstractMongoRecord.normalize_slug(slug)}'. Please slugify in the sheet.")
        has_slug_issues = True
if has_slug_issues:
    raise Exception("Issues found. See above errors.")

print("*** Deleting old authorTopic relationships ***")
link_query = {"generatedBy": "update_authors_data"}
print("links to delete", db.topic_links.count_documents(link_query))
db.topic_links.delete_many(link_query)
# Dependencies take too long here.  Getting rid of relationship dependencies above.  Assumption is that we'll import works right after to handle those dependencies.

def _(p: Topic, attr, value):
    if value:
        p.set_property(attr, value, "sefaria")

print("\n*** Updating authorTopic records ***\n")
for irow, l in enumerate(rows):
    slug = l[0].encode('utf8').decode()
    if len(slug.strip()) == 0: continue
    # print(slug)
    p = AuthorTopic.init(slug) or AuthorTopic()
    p.slug = slug
    p.title_group.add_title(l[1].strip(), "en", primary=True, replace_primary=True)
    p.title_group.add_title(l[3].strip(), "he", primary=True, replace_primary=True)
    for x in l[2].split(","):
        x = x.strip()
        if len(x):
            p.title_group.add_title(x, "en")
    for x in l[4].split(","):
        x = x.strip()
        if len(x):
            p.title_group.add_title(x, "he")
    if len(l[5]) > 0:
        if "c" in l[5]:
            _(p, 'birthYearIsApprox', True)
        else:
            _(p, 'birthYearIsApprox', False)
        m = re.search(r"\d+", l[5])
        if m:
            _(p, 'birthYear', m.group(0))
    if len(l[7]) > 0:
        if "c" in l[7]:
            _(p, 'deathYearIsApprox', True)
        else:
            _(p, 'deathYearIsApprox', False)
        m = re.search(r"\d+", l[7])
        if m:
            _(p, 'deathYear', m.group(0))
    _(p, "birthPlace", l[6])
    _(p, "deathPlace", l[8])
    _(p, "era", eras.get(l[9]))
    _(p, "enBio", l[10])
    _(p, "heBio", l[11])
    _(p, "enWikiLink", l[12])
    _(p, "heWikiLink", l[13])
    _(p, "jeLink", l[14])
    _(p, "sex", l[24])
    if p.get_property('enBio') or p.get_property('heBio'):
        p.description = {
            'en': p.get_property('enBio'),
            'he': p.get_property('heBio')
        }
        p.description_published = True
    p.save()

    # metadata links
    try:
        IntraTopicLink({
            "toTopic": 'authors',
            "fromTopic": p.slug,
            "generatedBy": "update_authors_data",
            "dataSource": "sefaria",
            "linkType": "displays-under"
        }).save()
    except DuplicateRecordError as e:
        print(e)

    if p.get_property('era'):
        to_topic = isa_object_aggregate_map.get(p.slug, era_slug_map[p.get_property('era')])
        itl = IntraTopicLink({
            "toTopic": to_topic,
            "fromTopic": p.slug,
            "linkType": "is-a",
            "dataSource": "sefaria",
            "generatedBy": "update_authors_data"
        })
        try:
            itl.save()
        except DuplicateRecordError as e:
            print(e)

#Second Pass
rowmap = {
    16: 'child-of',
    17: 'grandchild-of',
    18: 'child-in-law-of',
    19: 'sibling-in-law-of',
    20: 'taught',
    21: 'member-of',
    22: 'corresponded-with',
    23: 'opposed',
    24: 'cousin-of',
}
flip_link_dir = {'taught'}
print("\n*** Adding relationships ***\n")
for l in rows:
    from_slug = l[0].encode('utf8').decode()
    p = AuthorTopic.init(from_slug)
    for i, link_type_slug in rowmap.items():
        if l[i]:
            for pkey in l[i].split(","):
                to_slug = pkey.strip().encode('utf8').decode()
                to_slug, from_slug = (from_slug, to_slug) if link_type_slug in flip_link_dir else (to_slug, from_slug)
                # print("{} - {}".format(from_slug, to_slug))
                if AuthorTopic.init(to_slug) and AuthorTopic.init(from_slug):
                    try:
                        IntraTopicLink({
                            "toTopic": to_slug,
                            "fromTopic": from_slug,
                            "linkType": link_type_slug,
                            "dataSource": "sefaria",
                            "generatedBy" : "update_authors_data",
                        }).save()
                    except DuplicateRecordError:
                        continue
                
link_query = {"generatedBy": "update_authors_data"}
print("links created", db.topic_links.count_documents(link_query))