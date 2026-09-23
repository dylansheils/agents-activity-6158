// src/lib.rs

use std::cmp::Ordering;

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Version {
    pub major: u64,
    pub minor: u64,
    pub patch: u64,
    pub prerelease: Option<String>,
    pub build: Option<String>,
}

fn is_valid_number(s: &str) -> bool {
    if s.is_empty() {
        return false;
    }
    if s == "0" {
        return true;
    }
    if s.starts_with('0') {
        return false;
    }
    s.chars().all(|c| c.is_ascii_digit())
}

fn validate_prerelease(pr: &str) -> bool {
    // split by '.'
    for id in pr.split('.') {
        if id.is_empty() {
            return false;
        }
        if id.chars().all(|c| c.is_ascii_digit()) {
            // numeric identifier, no leading zeros unless "0"
            if !is_valid_number(id) {
                return false;
            }
        } else {
            // alphanumeric identifier: must contain at least one non-digit
            let mut has_non_digit = false;
            for c in id.chars() {
                if !(c.is_ascii_alphanumeric() || c == '-') {
                    return false;
                }
                if !c.is_ascii_digit() {
                    has_non_digit = true;
                }
            }
            if !has_non_digit {
                // all digits would have been caught above
                return false;
            }
        }
    }
    true
}

fn validate_build(build: &str) -> bool {
    for id in build.split('.') {
        if id.is_empty() {
            return false;
        }
        for c in id.chars() {
            if !(c.is_ascii_alphanumeric() || c == '-') {
                return false;
            }
        }
    }
    true
}

pub fn parse(s: &str) -> Result<Version, String> {
    // Split build
    let (without_build, build_opt) = if let Some(idx) = s.find('+') {
        let (left, right) = s.split_at(idx);
        let build_str = &right[1..]; // skip '+'
        if !validate_build(build_str) {
            return Err("invalid build identifier".to_string());
        }
        (left, Some(build_str.to_string()))
    } else {
        (s, None)
    };

    // Split prerelease
    let (core, prerelease_opt) = if let Some(idx) = without_build.find('-') {
        let (left, right) = without_build.split_at(idx);
        let pre_str = &right[1..]; // skip '-'
        if !validate_prerelease(pre_str) {
            return Err("invalid prerelease identifier".to_string());
        }
        (left, Some(pre_str.to_string()))
    } else {
        (without_build, None)
    };

    // Core should be major.minor.patch (all required)
    let parts: Vec<&str> = core.split('.').collect();
    if parts.len() != 3 {
        return Err("core version must have three dot separated parts".to_string());
    }
    let major_str = parts[0];
    let minor_str = parts[1];
    let patch_str = parts[2];
    if !(is_valid_number(major_str) && is_valid_number(minor_str) && is_valid_number(patch_str)) {
        return Err("invalid numeric component".to_string());
    }
    let major = major_str.parse::<u64>().map_err(|e| e.to_string())?;
    let minor = minor_str.parse::<u64>().map_err(|e| e.to_string())?;
    let patch = patch_str.parse::<u64>().map_err(|e| e.to_string())?;

    Ok(Version {
        major,
        minor,
        patch,
        prerelease: prerelease_opt,
        build: build_opt,
    })
}

pub fn to_string(v: &Version) -> String {
    let mut s = format!("{}.{}.{}", v.major, v.minor, v.patch);
    if let Some(pre) = &v.prerelease {
        s.push('-');
        s.push_str(pre);
    }
    if let Some(b) = &v.build {
        s.push('+');
        s.push_str(b);
    }
    s
}

fn compare_identifiers(a: &str, b: &str) -> Ordering {
    // Determine if numeric
    let a_numeric = a.chars().all(|c| c.is_ascii_digit());
    let b_numeric = b.chars().all(|c| c.is_ascii_digit());
    match (a_numeric, b_numeric) {
        (true, true) => {
            // compare as numbers
            let an = match a.parse::<u64>() {
                Ok(v) => v,
                Err(_) => 0, // unreachable due to validation
            };
            let bn = match b.parse::<u64>() {
                Ok(v) => v,
                Err(_) => 0,
            };
            an.cmp(&bn)
        }
        (true, false) => Ordering::Less,
        (false, true) => Ordering::Greater,
        (false, false) => a.cmp(b),
    }
}

pub fn compare(a: &Version, b: &Version) -> Ordering {
    // major, minor, patch
    match a.major.cmp(&b.major) {
        Ordering::Equal => {}
        ord => return ord,
    }
    match a.minor.cmp(&b.minor) {
        Ordering::Equal => {}
        ord => return ord,
    }
    match a.patch.cmp(&b.patch) {
        Ordering::Equal => {}
        ord => return ord,
    }
    // prerelease comparison
    match (&a.prerelease, &b.prerelease) {
        (None, None) => Ordering::Equal,
        (None, Some(_)) => Ordering::Greater, // release > prerelease
        (Some(_), None) => Ordering::Less,
        (Some(ap), Some(bp)) => {
            let a_parts: Vec<&str> = ap.split('.').collect();
            let b_parts: Vec<&str> = bp.split('.').collect();
            let max = a_parts.len().max(b_parts.len());
            for i in 0..max {
                let a_opt = a_parts.get(i);
                let b_opt = b_parts.get(i);
                match (a_opt, b_opt) {
                    (Some(aid), Some(bid)) => {
                        let ord = compare_identifiers(aid, bid);
                        if ord != Ordering::Equal {
                            return ord;
                        }
                    }
                    (Some(_), None) => return Ordering::Greater, // longer prerelease wins
                    (None, Some(_)) => return Ordering::Less,
                    (None, None) => break,
                }
            }
            Ordering::Equal
        }
    }
}

pub fn bump_major(v: &Version) -> Version {
    Version {
        major: v.major + 1,
        minor: 0,
        patch: 0,
        prerelease: None,
        build: None,
    }
}

pub fn bump_minor(v: &Version) -> Version {
    Version {
        major: v.major,
        minor: v.minor + 1,
        patch: 0,
        prerelease: None,
        build: None,
    }
}

pub fn bump_patch(v: &Version) -> Version {
    Version {
        major: v.major,
        minor: v.minor,
        patch: v.patch + 1,
        prerelease: None,
        build: None,
    }
}

// Placeholder for future functions
// fn placeholder() {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_basic() {
        let v = parse("1.2.3-alpha.1+build.9").unwrap();
        assert_eq!(v.major, 1);
        assert_eq!(v.minor, 2);
        assert_eq!(v.patch, 3);
        assert_eq!(v.prerelease, Some("alpha.1".to_string()));
        assert_eq!(v.build, Some("build.9".to_string()));
    }

    #[test]
    fn test_to_string_roundtrip() {
        let s = "2.0.0-beta+exp.sha.5114f85";
        let v = parse(s).unwrap();
        assert_eq!(to_string(&v), s);
    }

    #[test]
    fn test_compare_numbers() {
        let a = parse("1.0.0").unwrap();
        let b = parse("2.0.0").unwrap();
        assert_eq!(compare(&a, &b), Ordering::Less);
        assert_eq!(compare(&b, &a), Ordering::Greater);
    }

    #[test]
    fn test_compare_prerelease() {
        let r = parse("1.0.0").unwrap();
        let p = parse("1.0.0-alpha").unwrap();
        assert_eq!(compare(&r, &p), Ordering::Greater);
        assert_eq!(compare(&p, &r), Ordering::Less);
        let p2 = parse("1.0.0-alpha.1").unwrap();
        assert_eq!(compare(&p, &p2), Ordering::Less);
    }

    #[test]
    fn test_bump_major() {
        let v = parse("3.4.5").unwrap();
        let b = bump_major(&v);
        assert_eq!(to_string(&b), "4.0.0");
    }

    #[test]
    fn test_bump_minor() {
        let v = parse("3.4.5").unwrap();
        let b = bump_minor(&v);
        assert_eq!(to_string(&b), "3.5.0");
    }

    #[test]
    fn test_bump_patch() {
        let v = parse("3.4.5").unwrap();
        let b = bump_patch(&v);
        assert_eq!(to_string(&b), "3.4.6");
    }

    #[test]
    fn test_invalid_leading_zero() {
        assert!(parse("01.2.3").is_err());
        assert!(parse("1.02.3").is_err());
        assert!(parse("1.2.03").is_err());
    }

    #[test]
    fn test_invalid_prerelease() {
        assert!(parse("1.0.0-01").is_err()); // leading zero numeric
        assert!(parse("1.0.0-*").is_err()); // invalid char
    }
}
