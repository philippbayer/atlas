import click
import logging
import multiprocessing
import os
import sys
from atlas import __version__
from atlas.conf import make_config
from atlas.parsers import refseq_parser
from atlas.tables import merge_tables
from atlas.workflows import assemble, download


logging.basicConfig(level=logging.INFO, datefmt="%Y-%m-%d %H:%M", format="[%(asctime)s %(levelname)s] %(message)s")


@click.group(context_settings=dict(help_option_names=["-h", "--help"]))
@click.version_option(__version__)
@click.pass_context
def cli(obj):
    """ATLAS"""


@cli.command("refseq", short_help="enables tree based LCA and LCA star methods")
@click.argument("tsv", type=click.Path(exists=True))
@click.argument("namemap", type=click.Path(exists=True))
@click.argument("treefile", type=click.Path(exists=True))
@click.argument("output", type=click.File("w", atomic=True))
@click.option("-s", "--summary-method", type=click.Choice(["lca", "majority", "best"]), default="lca", show_default=True, help="summary method for annotating ORFs; when using LCA, it's recommended that one limits the number of hits using --top-fraction though function will be assigned per the best hit; 'best' is fastest")
@click.option("-a", "--aggregation-method", type=click.Choice(["lca", "lca-majority", "majority"]), default="lca-majority", show_default=True, help="summary method for aggregating ORF taxonomic assignments to contig level assignment; 'lca' will result in most stringent, least specific assignments")
@click.option("--majority-threshold", type=float, default=0.51, show_default=True, help="constitutes a majority fraction at tree node for 'lca-majority' ORF aggregation method")
@click.option("--min-identity", type=int, default=70, show_default=True, help="minimum allowable percent ID of BLAST hit")
@click.option("--min-bitscore", type=int, default=0, show_default=True, help="minimum allowable bitscore of BLAST hit; 0 disables")
@click.option("--min-length", type=int, default=60, show_default=True, help="minimum allowable BLAST alignment length")
@click.option("--max-evalue", type=float, default=0.000001, show_default=True, help="maximum allowable e-value of BLAST hit")
@click.option("--max-hits", type=int, default=10, show_default=True, help="maximum number of BLAST hits to consider when summarizing ORFs; can drastically alter ORF LCA assignments if too high without further limits")
@click.option("--table-name", default="refseq", help="table name within namemap database; expected columns are 'name', 'function', and 'taxonomy'")
@click.option("--top-fraction", type=float, default=1, show_default=True, help="filters ORF BLAST hits by only keep hits within this fraction of the highest bitscore; this is recommended over --max-hits")
def run_refseq_parser(tsv, namemap, treefile, output, summary_method, aggregation_method, majority_threshold, min_identity, min_bitscore, min_length, max_evalue, max_hits, table_name, top_fraction):
    """Parse TSV (tabular BLAST output [-outfmt 6]), grabbing taxonomy metadata from ANNOTATION to
    compute LCAs.

    The BLAST hits are assumed to be sorted by query with decreasing bitscores (best alignment first):

        \b
        sort -k1,1 -k12,12rn tsv > sorted_tsv

    Annotation file should include your BLAST subject sequence ID, a function, a taxonomy name,
    the taxonomy ID, and the parent taxonomy ID. This file is generated from `prepare-refseq`:

        \b
        gi|507051347|ref|WP_016122340.1| two-component sensor histidine kinase Bacillus cereus 1396 86661
        gi|507052147|ref|WP_016123132.1| two-component sensor histidine kinase Bacillus cereus 1396 86661
        gi|507053266|ref|WP_016124222.1| two-component sensor histidine kinase Bacillus cereus 1396 86661

    The RefSeq function is always derived from the best BLAST hit.

    The output will give contig, ORF ID, the lineage assigned to the contig based on
    --aggregation-method, the probability of error (erfc), taxonomy assigned to the ORF, the
    best hit's product, the best hit's evalue, and the best hit's bitscore:

        \b
        contig     orf          taxonomy erfc orf_taxonomy refseq               refseq_evalue refseq_bitscore
        k121_52126 k121_52126_1 root     1.0  root         hypothetical protein 1.0e-41       176.8

    """
    refseq_parser(tsv, namemap, treefile, output, summary_method, aggregation_method, majority_threshold, min_identity, min_bitscore, min_length, max_evalue, max_hits, table_name, top_fraction)


@cli.command("gff2tsv", short_help="writes version of Prokka TSV with contig as new first column")
@click.argument("gff", type=click.Path(exists=True))
@click.argument("output", type=click.File("w", atomic=True))
@click.option("--feature-type", default="CDS", show_default=True, help="feature type in GFF annotation to print")
def run_gff_to_tsv(gff, output, feature_type):
    import re
    locus_tag_re = re.compile(r"locus_tag=(.*?)(?:;|$)")
    ec_re = re.compile(r"eC_number=(.*?)(?:;|$)")
    gene_re = re.compile(r"gene=(.*?)(?:;|$)")
    product_re = re.compile(r"product=(.*?)(?:;|$)")

    # print the header into the output file
    print("contig_id", "locus_tag", "ftype", "gene", "EC_number", "product", sep="\t", file=output)

    with open(gff) as gff_fh:
        for line in gff_fh:
            if line.startswith("##FASTA"):
                break
            if line.startswith("#"):
                continue
            toks = line.strip().split("\t")
            if not toks[2] == feature_type:
                continue
            try:
                locus_tag = locus_tag_re.findall(toks[-1])[0]
            except IndexError:
                locus_tag = ""
            if not locus_tag:
                logging.critical("Unable to locate a locus tag in [%s]" % toks[-1])
                sys.exit(1)
            try:
                gene = gene_re.findall(toks[-1])[0]
            except IndexError:
                gene = ""
            try:
                ec_number = ec_re.findall(toks[-1])[0]
            except IndexError:
                ec_number = ""
            try:
                product = product_re.findall(toks[-1])[0]
            except IndexError:
                product = ""
            print(toks[0], locus_tag, toks[2], gene, ec_number, product, sep="\t", file=output)


@cli.command("munge-blast", short_help="adds contig ID to prokka annotated ORFs")
@click.argument("tsv", type=click.Path(exists=True))
@click.argument("gff", type=click.Path(exists=True))
@click.argument("output", type=click.File("w", atomic=True))
@click.option("--gene-id", default="ID", show_default=True, help="tag in gff attributes corresponding to ORF ID")
def run_munge_blast(tsv, gff, output, gene_id):
    """Prokka ORFs are reconnected to their origin contigs using the GFF of the Prokka output.
    Contig output is re-inserted as column 1, altering blast hits to be tabular + an extra initial
    column that will be used to place the ORFs into context.
    """
    import re
    gff_map = dict()

    logging.info("step 1 of 2; parsing %s" % gff)
    # gff attrs: ID=Flavobacterium_00802;inference=ab initio prediction:Prodigal:2.60;...
    orf_id_re = re.compile(r"%s=(.*?)\;" % gene_id)
    with open(gff) as prokka_gff:
        for line in prokka_gff:
            if line.startswith("##FASTA"):
                break
            if line.startswith("#"):
                continue
            toks = line.strip().split("\t")
            try:
                orf_id = orf_id_re.findall(toks[-1])[0]
            except IndexError:
                # some, like repeat regions, will not have a locus_tag=, but they also will not
                # be in the .faa file that is being locally aligned
                logging.warning("Unable to locate ORF ID using '%s' for line '%s'" % (gene_id, " ".join(toks)))
                continue
            gff_map[orf_id] = toks[0]

    logging.info("step 2 of 2; parsing %s" % tsv)
    # example blast hit:
    # Flavobacterium_00002	gi|500936490|ref|WP_012025625.1|	100.0	187	0	0	1	187	1	187	1.7e-99	369.8
    with open(tsv) as blast_hits:
        for line in blast_hits:
            toks = line.strip().split("\t")
            try:
                toks.insert(0, gff_map[toks[0]])
            except KeyError:
                logging.critical("%s was not found in the GFF [%s]" % (toks[0], gff))
                logging.critical("processing of %s was halted" % tsv)
                sys.exit(1)
            print(*toks, sep="\t", file=output)


@cli.command("merge-tables", short_help="merge Prokka TSV, Counts, and Taxonomy")
@click.argument("prokkatsv", type=click.Path(exists=True))
@click.argument("refseqtsv", type=click.Path(exists=True))
@click.argument("output")
@click.option("--counts", type=click.Path(exists=True), help="Feature Counts result TSV")
def run_merge_tables(prokkatsv, refseqtsv, output, counts):
    """Combines Prokka TSV, RefSeq TSV, and Counts TSV into a single table, merging on locus tag.
    """
    merge_tables(prokkatsv, refseqtsv, output, counts)


@cli.command("make-config", short_help="prepopulate a configuration file with samples and defaults")
@click.argument("config")
@click.argument("path")
@click.option("--data-type", default="metagenome", type=click.Choice(["metagenome", "metatranscriptome"]),
              show_default=True, help="sample data type")
@click.option("--database-dir", default="databases", show_default=True,
              help="location of formatted databases (from `atlas download`)")
@click.option("--threads", default=None, type=int,
              help="number of threads to use per multi-threaded job")
@click.option("--assembler", default="megahit", type=click.Choice(["megahit", "spades"]),
              show_default=True, help="contig assembler")
def run_make_config(config, path, data_type, database_dir, threads, assembler):
    """Write the file `config` and complete the sample names and paths for all FASTQ files in
    `path`.

    `path` is traversed recursively and adds any file with '.fastq' or '.fq' extension with the
    file name as the sample ID. Any single-end (non-interleaved) FASTQs under `path` will cause
    errors if left in the configuration file.
    """
    make_config(config, path, data_type, database_dir, threads, assembler)


@cli.command("assemble", context_settings=dict(ignore_unknown_options=True), short_help="assembly workflow")
@click.argument("config")
@click.option("-j", "--jobs", default=multiprocessing.cpu_count(), type=int, show_default=True, help="use at most this many cores in parallel; total running tasks at any given time will be jobs/threads")
@click.option("-o", "--out-dir", default=os.path.realpath("."), show_default=True, help="results output directory")
@click.option("--dryrun", is_flag=True, default=False, show_default=True, help="do not execute anything")
@click.option("--tmpdir")
@click.argument("snakemake_args", nargs=-1, type=click.UNPROCESSED)
def run_assemble(config, jobs, out_dir, dryrun, snakemake_args):
    assemble(os.path.realpath(config), jobs, out_dir, dryrun, snakemake_args)


@cli.command("annotate", short_help="annotation workflow")
@click.option("--config-file")
@click.option("--sample", multiple=True, nargs=-1, help="sample definitions, FASTA file path, and optionally, FASTQ file path(s)")
@click.option("--single-end", is_flag=True, default=False, show_default=True, help="use this flag when quantifying input of single-end sample(s); run single- and paired-end sequences in separate instances")
@click.option("--tmpdir", help="temporary working directory")
@click.option("--diamond-db", help="path to .dmnd file")
@click.option("--diamond-top-seqs", type=int, default=2, show_default=True, help="filter local alignments to within this percent of the highest observed bitscore")
@click.option("--diamond-e-value", type=float, default=0.000001, show_default=True, help="minimum passing e-value to report an alignment")
@click.option("--diamond-min-identity", type=int, default=50, show_default=True, help="minimum percent identity to report an alignment")
@click.option("--diamond-query-coverage", type=int, default=60, show_default=True, help="minimum query percent coverage to report an alignment")
@click.option("--diamond-gap-open", type=int, default=11, show_default=True, help="gap open penalty")
@click.option("--diamond-gap-extend", type=int, default=1, show_default=True, help="gap extend penalty")
@click.option("--diamond-block-size", type=int, default=2, show_default=True, ),
index_chunks = config.get("diamond_index_chunks", 4),
run_mode = "--more-sensitive" if not config.get("diamond_run_mode", "") == "fast" else ""

def run_annotate(config_file, sample, single_end):
    """

    Example interleaved:

        atlas annotate --sample sample1 sample1_contigs.fasta sample1_pe.fastq.gz

    Example multi-sample, interleaved:

        atlas annotate --sample sample1 sample1_contigs.fasta sample1_pe.fastq.gz \
            --sample sample2 sample2_contigs.fasta sample2_pe.fastq.gz

    Example paired-end:

        atlas annotate --sample sample1 sample1_contigs.fasta sample1_R1.fastq.gz sample1_R2.fastq.gz

    Example single-end:

        atlas annotate --sample sample1 sample1_contigs.fasta sample1_se.fastq.gz

    """



@cli.command("download", context_settings=dict(ignore_unknown_options=True), short_help="download reference files")
@click.option("-j", "--jobs", default=multiprocessing.cpu_count(), type=int, show_default=True, help="use at most this many cores in parallel; total running tasks at any given time will be jobs/threads")
@click.option("-o", "--out-dir", default=os.path.join(os.path.realpath("."), "databases"), show_default=True, help="database download directory")
@click.argument("snakemake_args", nargs=-1, type=click.UNPROCESSED)
def run_download(jobs, out_dir, snakemake_args):
    download(jobs, out_dir, snakemake_args)


if __name__ == "__main__":
    cli()
