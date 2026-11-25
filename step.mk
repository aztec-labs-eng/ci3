# ci3 steps: make targets whose cache key ci3 derives from the make dependency graph (see ci3/step).
#
# Include it in a repository's Makefile once ROOT is the repository root:
#   include $(ROOT)/ci3/step.mk
#
# A step names its own inputs, and make's graph supplies the rest of its key:
#   l1-contracts-src: private INPUTS := l1-contracts/.rebuild_patterns
#   l1-contracts-src: l1-contracts-solc | l1-contracts-format-check
#   	$(call step,$@,l1-contracts,build_src,out cache)
#
# - Normal prerequisites (before |) are part of the key: the step uses what they produce.
# - Order-only prerequisites (after |) only run first, as a check or a warm-up does.
# - Targets may also set KEY_ENV (names of variables whose values change the outputs) and KEY_SALT.
# - `private` stops make passing a target's variables down to its prerequisites, which it otherwise does.
# - A target that only groups others, when a step depends on it, needs the recipe $(call step_group,$@).
# - A recipe that is not a step yet runs through ci3/step_legacy, so steps can depend on it.

CI3 ?= $(ROOT)/ci3

# One directory of keys per make run, shared with every recipe and with any make started under it.
ifndef CI3_KEYS_DIR
CI3_KEYS_DIR := $(shell mktemp -d "$${TMPDIR:-/tmp}/ci3-keys.XXXXXX")
endif
export CI3_KEYS_DIR

# $(call step,<target>,<project dir>,<bootstrap command>,<outputs>)
#   <project dir>        relative to ROOT
#   <bootstrap command>  run as ./bootstrap.sh <bootstrap command> on a cache miss
#   <outputs>            paths restored on a hit, relative to the project; - for a check; never to always run
define step
	@cd $(ROOT)/$(2) && INPUTS='$(INPUTS)' AFTER='$|' KEY_ENV='$(KEY_ENV)' KEY_SALT='$(KEY_SALT)' \
	  $(CI3)/color_prefix $(1) "$(CI3)/step $(1) '$(3)' '$(4)' $^"
endef

# $(call step_group,<target>): a target that only groups its prerequisites; its key is theirs.
define step_group
	@$(CI3)/step $(1) - never $^
endef
