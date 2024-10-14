Contributing Guidelines
=======================

### Git Workflow

1. Every pull request must be linked to an issue, without any
   exceptions.
2. Ensure that your branch contains logical [atomic
   commits](https://www.pauline-vos.nl/atomic-commits/).
3. Write commit messages following the [alphagov Git
   styleguide](https://github.com/alphagov/styleguides/blob/master/git.md).
4. Pull requests must contain a short description of your
   solution.
5. Branch naming convention: person/target-branch/#issue-
   description-of-branch.
    1. person - The name of the owner of the branch. For
       example, Jur, Rokaya, Shaaer, Denis, etc.
    2. target-branch - A reference to the target branch you
       want to merge into.
    3. #issue - Every branch must be linked to a GitHub issue.
       Enter the issue number here.
    4. description-of-branch - Describe what's inside, for
       example, "fix-for-jumping-controls-bug" or
       "new-icon-set-for-parameter-definition".
6. Pull requests must not contain any formatting or
    refactoring changes that are outside the scope of the
    issue.
7. New features require regression test coverage. (@todo)

### Code Ownership

@jjroelofs is the code owner in this repository, and no pull
requests can be merged without his review.

### Coding Standards

1. @todo

Coding standards are automatically checked when you create a
Pull Request. You can also run code linters locally using the
instructions here: @todo
