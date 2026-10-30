# Imported AI tool review

Reviewed all source modules and accompanying documentation before removing the imported directory.

- `dense.py`, `layer.py`, `activation.py`: adapted dense forward/backpropagation and nonlinear activation math into `neural_network.py`. Fixed input-gradient computation using updated rather than original weights. Replaced global random initialization with a local deterministic generator.
- `losses.py`, `main.py`: adapted weighted cross-entropy training to a binary sigmoid target. Source used multiclass softmax, dense 128/64 hidden layers, EEG labels and saved NPZ weights. Trading uses nine bounded existing features and eight tanh hidden units; no source weights are loaded.
- `convolution.py`, `reshape.py`: reviewed; unnecessary for the existing tabular trading features.
- `main.py`, `data_explorer.py`, `config.py`: reviewed EEG downloads, synthetic fallback, filtering, normalization, splitting, evaluation, weight loading/saving and cleanup. These paths are excluded. In particular, source normalization preceded splitting and random splits do not provide chronological trading validation. Existing trading feature transforms use no fitted future statistics.
- `test.py`, `weight-checker.py`: source dependency/NPZ inspection utilities, not model correctness tests. Replaced with numerical gradient, nonlinear XOR, JSON persistence and invalid-schema checks.
- Requirements, ignore rules and guides: reviewed; EEG/scientific framework dependencies and artifacts were not imported. Implementation uses Python standard library only.

The shared model rebuilds from eligible paper closes across all runs, confirmed bot closes and contiguous receipt-backed cycles from configured saved wallets. Completed archived/reset paper wins and losses remain training labels; balance refills are excluded. Paper remains modeled data. The neural score remains shadow-only until prospective frozen-candidate qualification; qualified models may veto entries, never authorize a blocked entry or change risk size. See MEME_TRADER.md for qualification and rollback. Mathematical tests establish backpropagation correctness, not trading profitability or calibrated predictions.
