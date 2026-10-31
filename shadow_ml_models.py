
"""CPU-only SVM and LSTM models for unified learning.

No network access, order execution, paid APIs, or GPU dependencies.
All inputs are bounded and finite. Training is deterministic.
"""
import math
import random

MAX_ROWS = 1024
MAX_INPUTS = 128
MAX_STEPS = 8
HIDDEN = 4


def sigmoid(x):
    x = max(-30.0, min(30.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def vector(x):
    if (not isinstance(x, list) or not 1 <= len(x) <= MAX_INPUTS
            or any(type(v) not in (int, float)
                   or not math.isfinite(v) or abs(v) > 1 for v in x)):
        raise ValueError("Expected bounded normalized feature vector")
    return x


def rows(samples, sequential=False):
    if not 1 <= len(samples) <= MAX_ROWS:
        raise ValueError("Sample budget exceeded")
    size = None
    for x, y in samples:
        if type(y) is not int or y not in (0, 1):
            raise ValueError("Verified binary target required")
        points = x if sequential else [x]
        if sequential and not 1 <= len(points) <= MAX_STEPS:
            raise ValueError("Sequence budget exceeded")
        for point in points:
            vector(point)
            if size is None:
                size = len(point)
            if len(point) != size:
                raise ValueError("Feature size mismatch")
    return size


def fit_svm(samples, epochs=20, rate=0.01, penalty=0.01):
    size = rows(samples)
    weights = [0.0] * size
    bias = 0.0
    for _ in range(epochs):
        for x, label in samples:
            y = 1 if label else -1
            margin = y * (sum(a * b for a, b in zip(weights, x)) + bias)
            gradients = [
                penalty * w - (y * v if margin < 1 else 0.0)
                for w, v in zip(weights, x)
            ]
            weights = [
                max(-8.0, min(8.0, w - rate * g))
                for w, g in zip(weights, gradients)
            ]
            if margin < 1:
                bias = max(-8.0, min(8.0, bias + rate * y))
    return {
        "family": "svm",
        "weights": weights,
        "bias": bias,
        "calibration": None,
    }


def margin(model, x):
    vector(x)
    if len(x) != len(model["weights"]):
        raise ValueError("Feature size mismatch")
    return sum(a * b for a, b in zip(model["weights"], x)) + model["bias"]


def calibrate_svm(model, samples):
    rows(samples)
    if not samples or len({y for _, y in samples}) < 2:
        raise ValueError("Independent two-class calibration required")
    a, b = 1.0, 0.0
    for _ in range(50):
        for x, label in samples:
            m = margin(model, x)
            error = sigmoid(a * m + b) - label
            a = max(0.0, min(8.0, a - 0.01 * error * m))
            b = max(-8.0, min(8.0, b - 0.01 * error))
    return {**model, "calibration": [a, b]}


def initialize_lstm(size):
    if type(size) is not int or not 1 <= size <= MAX_INPUTS:
        raise ValueError("Invalid input width")
    rng = random.Random(42)
    return {
        "family": "lstm",
        "size": size,
        "weights": [
            [
                [rng.uniform(-0.1, 0.1) for _ in range(size + HIDDEN)]
                for _ in range(HIDDEN)
            ]
            for _ in range(4)
        ],
        "bias": [
            [1.0 if gate == 1 else 0.0 for _ in range(HIDDEN)]
            for gate in range(4)
        ],
        "output": [rng.uniform(-0.1, 0.1) for _ in range(HIDDEN)],
        "output_bias": 0.0,
    }


def forward_lstm(model, sequence):
    if not isinstance(sequence, list) or not 1 <= len(sequence) <= MAX_STEPS:
        raise ValueError("Invalid LSTM window")
    h, c, cache = [0.0] * HIDDEN, [0.0] * HIDDEN, []
    for x in sequence:
        vector(x)
        if len(x) != model["size"]:
            raise ValueError("LSTM feature size mismatch")
        joined, previous = x + h, c[:]
        gates = []
        for gate in range(4):
            logits = [
                sum(w * v for w, v in zip(weights, joined)) + bias
                for weights, bias in zip(
                    model["weights"][gate], model["bias"][gate]
                )
            ]
            gates.append([
                math.tanh(v) if gate == 3 else sigmoid(v) for v in logits
            ])
        incoming, forget, output, proposal = gates
        c = [f * old + i * g for f, old, i, g in
             zip(forget, previous, incoming, proposal)]
        h = [o * math.tanh(v) for o, v in zip(output, c)]
        cache.append((joined, previous, gates, c[:], h[:]))
    probability = sigmoid(
        sum(w * v for w, v in zip(model["output"], h))
        + model["output_bias"]
    )
    return probability, cache


def fit_lstm(samples, epochs=5, rate=0.005):
    size = rows(samples, sequential=True)
    model = initialize_lstm(size)

    def update(value, gradient):
        gradient = max(-1.0, min(1.0, gradient))
        return max(-8.0, min(8.0, value - rate * gradient))

    for _ in range(epochs):
        for sequence, label in samples:
            probability, cache = forward_lstm(model, sequence)
            error = probability - label
            output_weights = model["output"][:]
            dw = [
                [[0.0] * (size + HIDDEN) for _ in range(HIDDEN)]
                for _ in range(4)
            ]
            db = [[0.0] * HIDDEN for _ in range(4)]
            dh = [error * w for w in output_weights]
            dc = [0.0] * HIDDEN

            for joined, previous, gates, cell, hidden in reversed(cache):
                incoming, forget, output, proposal = gates
                total = [
                    dc[j] + dh[j] * output[j] *
                    (1 - math.tanh(cell[j]) ** 2)
                    for j in range(HIDDEN)
                ]
                derivatives = [
                    [
                        total[j] * proposal[j] * incoming[j] *
                        (1 - incoming[j])
                        for j in range(HIDDEN)
                    ],
                    [
                        total[j] * previous[j] * forget[j] *
                        (1 - forget[j])
                        for j in range(HIDDEN)
                    ],
                    [
                        dh[j] * math.tanh(cell[j]) * output[j] *
                        (1 - output[j])
                        for j in range(HIDDEN)
                    ],
                    [
                        total[j] * incoming[j] *
                        (1 - proposal[j] ** 2)
                        for j in range(HIDDEN)
                    ],
                ]
                djoined = [0.0] * (size + HIDDEN)
                for gate in range(4):
                    for unit in range(HIDDEN):
                        grad = derivatives[gate][unit]
                        db[gate][unit] += grad
                        for index, value in enumerate(joined):
                            dw[gate][unit][index] += grad * value
                            djoined[index] += (
                                grad * model["weights"][gate][unit][index]
                            )
                dh = djoined[size:]
                dc = [total[j] * forget[j] for j in range(HIDDEN)]

            last_hidden = cache[-1][-1]
            for gate in range(4):
                for unit in range(HIDDEN):
                    model["weights"][gate][unit] = [
                        update(w, grad)
                        for w, grad in zip(
                            model["weights"][gate][unit], dw[gate][unit]
                        )
                    ]
                    model["bias"][gate][unit] = update(
                        model["bias"][gate][unit], db[gate][unit]
                    )
            model["output"] = [
                update(w, error * h)
                for w, h in zip(output_weights, last_hidden)
            ]
            model["output_bias"] = update(model["output_bias"], error)
    return model


def predict(model, x):
    if model["family"] == "svm":
        if model.get("calibration") is None:
            raise ValueError("Uncalibrated SVM")
        a, b = model["calibration"]
        return sigmoid(a * margin(model, x) + b)
    if model["family"] == "lstm":
        return forward_lstm(model, x)[0]
    raise ValueError("Unsupported model family")
