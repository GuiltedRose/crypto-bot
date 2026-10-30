# Imported AI tool review

Reviewed all source modules and accompanying documentation before removing the imported directory.

- `dense.py`, `layer.py`, `activation.py`: adapted dense forward/backpropagation and nonlinear activation math into `neural_network.py`. Fixed input-gradient computation using updated rather than original weights. Replaced global random initialization with a local deterministic generator.
- `losses.py`, `main.py`: adapted weighted cross-entropy training to a binary sigmoid target. Source used multiclass softmax, dense 128/64 hidden layers, EEG labels and saved NPZ weights. Trading uses 36 bounded feature values and eight tanh hidden units; no source weights are loaded.
- `convolution.py`, `reshape.py`: reviewed; unnecessary for the existing tabular trading features.
- `main.py`, `data_explorer.py`, `config.py`: reviewed EEG downloads, synthetic fallback, filtering, normalization, splitting, evaluation, weight loading/saving and cleanup. These paths are excluded. In particular, source normalization preceded splitting and random splits do not provide chronological trading validation. Existing trading feature transforms use no fitted future statistics.
- `test.py`, `weight-checker.py`: source dependency/NPZ inspection utilities, not model correctness tests. Replaced with numerical gradient, nonlinear XOR, JSON persistence and invalid-schema checks.
- Requirements, ignore rules and guides: reviewed; EEG/scientific framework dependencies and artifacts were not imported. Implementation uses Python standard library only.

The shared model rebuilds from eligible paper closes across all runs, confirmed bot closes and contiguous receipt-backed cycles from configured saved wallets. Completed archived/reset paper wins and losses remain training labels; balance refills are excluded. Paper remains modeled data. The default online learning mode can veto entries after 30 samples; it is experimental and has not passed prospective qualification. The optional qualified mode waits for frozen-candidate validation. Neither mode authorizes a blocked entry or changes live risk size. Paper adds bounded exploration and concurrent positions sharing one bankroll. See MEME_TRADER.md for qualification and rollback. Mathematical tests establish backpropagation correctness, not trading profitability or calibrated predictions.

Positive net outcomes receive a 2x multiplier on top of inverse-frequency class
weights; losses and break-even outcomes retain negative labels. The neural veto
threshold is 2/3 to compensate for that multiplier, with prospective qualification
required in the optional qualified mode. Scores are uncalibrated. Model v4 rebuilds from eligible outcomes
and invalidates v1 candidates rather than mixing training policies.

Model v4 uses class-normalized full-batch training and completed-chart features.
An independent continuation network learns from recorded one-minute changes in
net position quotes; it is a predictive association, not causal loss analysis.

Exit models now require frozen, chronological position-grouped OOS evaluation
and champion approval. Matching rule-close outcomes provide estimated paired
net improvements; neural-closed positions without a rule baseline are excluded.
All valid historical data remains available, and worsening champions roll back.
See MEME_TRADER.md for the lifecycle and quote-replay limitations.
