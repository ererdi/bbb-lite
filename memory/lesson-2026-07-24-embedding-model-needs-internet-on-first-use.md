---
title: Embedding model needs internet on first use
date: 2026-07-24
confidence: high
---

`sentence-transformers`'ın `all-MiniLM-L6-v2` modeli offline-first değildir. `mc.py`'deki
`_get_embedding_model()` bir makinede ilk çağrıldığında Hugging Face Hub'dan model
ağırlıklarını indirmesi gerekir; o an internet yoksa `ConnectionError` türünden bir hata
fırlatır. Model bir kere `~/.cache/huggingface` altına inince sonraki çağrılar tamamen
offline çalışır, tekrar network gerekmez.

Bunun somut etkisi: bu proje `ask_ollama()` gibi fail-soft değil — model yoksa/inemezse
çağıran taraf (`_embed()`, `index_memory_files()`, `semantic_search()`) hata alır, sessizce
None dönmez. Testler bu yüzden `SemanticSearchTestCase.setUp()` içinde modeli önceden
yüklemeyi dener; başarısız olursa (network yok ve yerel cache de yoksa) testi `error`
yerine `unittest.skip` ile atlar.
