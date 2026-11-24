//
// Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
// SPDX-License-Identifier: Apache-2.0
//
//! What the tests share: a source of UUIDs, a number that is a function of its parts, and a small random generator, so that a run of
//! the tests is the same every time.
#![allow(dead_code)]

use sha2::{Digest, Sha256};

pub const RUN: &str = "11111111-1111-4111-8111-111111111111";

/// A source of UUIDs that count: `<prefix>-0000-4000-8000-<n in 12 hex digits>`.
pub fn uuids(prefix: &str) -> impl FnMut() -> String {
    let prefix = prefix.to_string();
    let mut n = 0u64;
    move || {
        n += 1;
        format!("{prefix}-0000-4000-8000-{n:012x}")
    }
}

/// A number between `lo` and `hi`, with one decimal, that is a function of `seed` and `parts` only.
pub fn number(seed: u64, parts: &[&str], lo: f64, hi: f64) -> f64 {
    let digest = Sha256::digest(format!("{seed}|{}", parts.join("|")).as_bytes());
    let n = u32::from_be_bytes([digest[0], digest[1], digest[2], digest[3]]);
    ((lo + (hi - lo) * f64::from(n) / 4_294_967_296.0) * 10.0).round() / 10.0
}

/// A small, fast random generator (xorshift64*): the same seed gives the same sequence.
pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Self {
        Rng(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1)
    }

    pub fn next_u64(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }

    /// A number below `n`.
    pub fn below(&mut self, n: usize) -> usize {
        (self.next_u64() % n as u64) as usize
    }

    /// A number between 0 and 1.
    pub fn unit(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / (1u64 << 53) as f64
    }

    /// An element of `items`.
    pub fn choose<'a, T>(&mut self, items: &'a [T]) -> &'a T {
        &items[self.below(items.len())]
    }
}

/// A `Cell` that can be shared with an agent (a clock and a source of UUIDs have to be `Send`): clones share one value.
pub struct SCell<T>(std::sync::Arc<std::sync::Mutex<T>>);

impl<T: Copy> SCell<T> {
    pub fn new(v: T) -> Self {
        SCell(std::sync::Arc::new(std::sync::Mutex::new(v)))
    }

    pub fn get(&self) -> T {
        *self.0.lock().unwrap()
    }

    pub fn set(&self, v: T) {
        *self.0.lock().unwrap() = v;
    }
}

impl<T: Copy + Default> Default for SCell<T> {
    fn default() -> Self {
        SCell::new(T::default())
    }
}

impl<T> Clone for SCell<T> {
    fn clone(&self) -> Self {
        SCell(self.0.clone())
    }
}

/// A `RefCell` that can be shared with an agent: clones share one value.
pub struct SRef<T>(std::sync::Arc<std::sync::Mutex<T>>);

impl<T> SRef<T> {
    pub fn new(v: T) -> Self {
        SRef(std::sync::Arc::new(std::sync::Mutex::new(v)))
    }

    pub fn borrow(&self) -> std::sync::MutexGuard<'_, T> {
        self.0.lock().unwrap()
    }

    pub fn borrow_mut(&self) -> std::sync::MutexGuard<'_, T> {
        self.0.lock().unwrap()
    }
}

impl<T: Default> Default for SRef<T> {
    fn default() -> Self {
        SRef::new(T::default())
    }
}

impl<T> Clone for SRef<T> {
    fn clone(&self) -> Self {
        SRef(self.0.clone())
    }
}
