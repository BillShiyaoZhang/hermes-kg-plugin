# Upstream vocabulary notices

The files in `upstream/` are unmodified publisher downloads, not new Hermes
definitions. `sources.lock.json` records their exact download URLs, release
versions, serialization formats and SHA-256 checksums. The compiler uses only
the terms selected in `profile.ttl` and their bounded inheritance dependencies.
It never follows `owl:imports` or fetches resources at runtime.

* **Schema.org 30.1**, published 2026-09-16. Copyright the Schema.org sponsors.
  The vocabulary is licensed under **Creative Commons Attribution-ShareAlike
  3.0**: https://creativecommons.org/licenses/by-sa/3.0/ . Publisher terms:
  https://schema.org/docs/terms.html . The Apache license of website software
  is not the license of this vocabulary. The original release file is retained.
* **SKOS, W3C Recommendation 18 August 2009**. Copyright © 2009 W3C
  (MIT, ERCIM, Keio). Original copyright notices remain in the RDF/XML file.
  W3C Document License:
  https://www.w3.org/Consortium/Legal/2002/copyright-documents-20021231 .
* **PROV-O, W3C Recommendation 30 April 2013**. Copyright © 2011–2013 W3C
  (MIT, ERCIM, Keio, Beihang). Original notices remain in the Turtle file.
  W3C Document License:
  https://www.w3.org/Consortium/Legal/2002/copyright-documents-20021231 .
* **DCMI Metadata Terms and DCMI Type Vocabulary, 2020-01-20**. Copyright
  Dublin Core Metadata Initiative. Licensed under **Creative Commons
  Attribution 4.0**: https://creativecommons.org/licenses/by/4.0/ . Publisher
  notice: https://www.dublincore.org/about/copyright/ . Original RDF notices
  remain in both Turtle files.

`profile.ttl` and the generated personal profile, including localized labels,
selection notes and adaptations of these vocabularies, are distributed under
CC BY-SA 3.0. They are distinct from the repository's software source license.
Attribution to the above publishers must accompany redistribution. No upstream
file has been edited. Generated selection and shape files are adaptations, and
are clearly identified as generated application artifacts rather than upstream
releases.
